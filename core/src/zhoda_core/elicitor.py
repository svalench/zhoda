"""Stage 0: smart elicitation.

Questions are asked ONLY when the ambiguity score clears the threshold
AND mode is smart. auto-clarify and below-threshold never invent facts:
unasked items land in open_ambiguities, never in assumptions. The score is
inter-model agreement, not self-report. Round-6 §4: the loop is closed via
the on_questions callback; without it, questions degrade to open_ambiguities.
Round-7 §4: apply_answers keeps EVERYTHING — answered questions become
constraints, unanswered ones become open_ambiguities (nothing is dropped).
Each turn asks at most ASK_BATCH questions; after answers the council is
asked again until it reports no remaining high-impact ambiguity, the user
skips, or max_turns. Unasked leftover at stop lands in open_ambiguities.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable

from pydantic import BaseModel, Field

from .models import ValueMap
from .providers.openrouter import OpenRouterProvider, make_cache_key
from .stage_dtos import DedupVote, ElicitVote, parse_stage

ASK_BATCH = 3
DEFAULT_MAX_ELICIT_TURNS = 4

ELICIT_PROMPT = """You are a council member. Do NOT answer the question.
List what is ambiguous or underspecified — things whose clarification would
change the answer. If known facts already suffice, return {{"ambiguities": []}}.

Question: {question}

Context:
{context}
{known_block}
Respond with ONLY valid JSON:
{{"ambiguities": [{{"ambiguity": "...", "why_it_matters": "...",
  "candidate_question": "...", "options": ["one-tap answer", "..."]}}]}}
If nothing is ambiguous, return {{"ambiguities": []}}."""

DEDUP_PROMPT = """Group equivalent clarifying questions. Same intent = one group,
even when the wording differs.

Questions:
{numbered}

ONLY valid JSON: {{"groups": [[0, 2], [1]]}}
Each index appears in exactly one group. Put the clearest wording first in
each group."""

# «что оцениваем / о чём речь»
_GROUNDING_QUESTION = re.compile(
    r"(what|which)\s+(project|repo|repository|codebase|document|artifact|system)\b"
    r"|what (are we|is being) evaluat"
    r"|о\s+ч(ё|е)м\s+речь"
    r"|какой\s+проект"
    r"|что\s+оценива"
    r"|о\s+каком",
    re.IGNORECASE,
)
_NEEDS_ARTIFACT = re.compile(
    r"(оцени|evaluate|review|assess).{0,60}(проект|project|repo|репозитор|"
    r"codebase|документ|document)"
    r"|(проект|project|repository|repo)\s+[A-Za-zА-Яа-я0-9_.-]+",
    re.IGNORECASE,
)
_URL = re.compile(r"https?://|www\.", re.IGNORECASE)
_DEIXIS = re.compile(
    r"\b(this|that)\s+(project|repo|repository|codebase|document|one)\b"
    r"|этот\s+проект|этот\s+репозитор|данн(ый|ого)\s+проект",
    re.IGNORECASE,
)


class ClarifyingQuestion(BaseModel):
    question: str
    why_it_matters: str
    options: list[str] = Field(default_factory=list)


class ElicitationResult(BaseModel):
    ambiguity_score: float
    questions: list[ClarifyingQuestion] = Field(default_factory=list)  # UI: ask these
    pending: list[ClarifyingQuestion] = Field(default_factory=list)  # next turn
    all_questions: list[ClarifyingQuestion] = Field(default_factory=list)  # grounding
    value_map: ValueMap = Field(default_factory=ValueMap)


class ElicitationSession(BaseModel):
    """Итог interview(): заданные вопросы, ответы, финальная value map."""

    value_map: ValueMap = Field(default_factory=ValueMap)
    questions: list[ClarifyingQuestion] = Field(default_factory=list)
    answers: list[str] = Field(default_factory=list)
    all_questions: list[ClarifyingQuestion] = Field(default_factory=list)
    turns: int = 0


class Elicitor:
    def __init__(self, provider: OpenRouterProvider, ambiguity_threshold: float = 0.6) -> None:
        self.provider = provider
        self.ambiguity_threshold = ambiguity_threshold  # TODO(calibrate): bench

    async def elicit(
        self,
        question: str,
        council: list[str],
        mode: str = "smart",
        *,
        context: str = "",
        dedup_model: str | None = None,
        known: ValueMap | None = None,
        already_asked: list[str] | None = None,
        ask_batch: int = ASK_BATCH,
    ) -> ElicitationResult:
        if mode == "no-clarify":
            return ElicitationResult(ambiguity_score=0.0)

        asked = list(already_asked or [])
        known_map = known or ValueMap()
        context_block = context.strip() or "(none)"
        known_block = _known_block(known_map, asked)
        results = await asyncio.gather(
            *(
                self.provider.ask_json(
                    m,
                    ELICIT_PROMPT.format(
                        question=question,
                        context=context_block,
                        known_block=known_block,
                    ),
                    cache_key=make_cache_key(
                        "elic", m, question, context_block, known_block, tuple(asked)
                    ),
                )
                for m in council
            ),
            return_exceptions=True,
        )
        payloads = []
        for raw in results:
            parsed = parse_stage(
                ElicitVote,
                raw if isinstance(raw, dict) else None,
                stage="elicit",
            )
            if parsed.value is None:
                continue
            payloads.append(parsed.value)
        if not payloads:
            raise RuntimeError("all council models failed at elicitation")

        flagged = [p for p in payloads if p.ambiguities]
        score = len(flagged) / len(payloads)

        ambiguities = [a.model_dump() for p in flagged for a in p.ambiguities]
        questions = [q for a in ambiguities if (q := _as_question(a)) is not None]
        questions = await self._dedup_questions(questions, dedup_model=dedup_model)
        questions = _prefer_grounding(questions)
        asked_keys = {_qkey(text) for text in asked}
        questions = [q for q in questions if _qkey(q.question) not in asked_keys]
        # auto-clarify / ниже порога: не спрашиваем и не выдаём вопросы за факты
        if mode == "auto-clarify" or score < self.ambiguity_threshold:
            return ElicitationResult(
                ambiguity_score=score,
                questions=[],
                all_questions=questions,
                value_map=ValueMap(open_ambiguities=[q.question for q in questions]),
            )
        batch = max(1, ask_batch)
        return ElicitationResult(
            ambiguity_score=score,
            questions=questions[:batch],
            pending=questions[batch:],
            all_questions=questions,
        )

    async def interview(
        self,
        question: str,
        council: list[str],
        mode: str = "smart",
        *,
        context: str = "",
        dedup_model: str | None = None,
        on_questions: Callable[[list[ClarifyingQuestion]], list[str]] | None = None,
        max_turns: int = DEFAULT_MAX_ELICIT_TURNS,
        stop_after_batch: Callable[[list[ClarifyingQuestion], list[str], ValueMap], bool]
        | None = None,
        on_turn: Callable[[int, ElicitationResult], None] | None = None,
    ) -> ElicitationSession:
        """Спрашивать пачками, пока совет не скажет «хватит», юзер не скипнет, или cap."""
        if mode == "no-clarify":
            return ElicitationSession()

        value_map = ValueMap()
        asked: list[ClarifyingQuestion] = []
        answers: list[str] = []
        discovered: list[ClarifyingQuestion] = []
        turns = 0
        for turn in range(1, max(1, max_turns) + 1):
            turns = turn
            result = await self.elicit(
                question,
                council,
                mode,
                context=context,
                dedup_model=dedup_model,
                known=value_map,
                already_asked=[q.question for q in asked],
            )
            if on_turn is not None:
                on_turn(turn, result)
            discovered = _extend_unique(discovered, result.all_questions)

            if mode == "auto-clarify" or not result.questions:
                value_map = merge_value_maps(value_map, result.value_map)
                break

            if on_questions is None:
                value_map = dump_unasked(value_map, result.questions + result.pending)
                discovered = _extend_unique(discovered, result.questions + result.pending)
                break

            padded = list(on_questions(result.questions))
            padded.extend([""] * max(0, len(result.questions) - len(padded)))
            padded = padded[: len(result.questions)]
            applied = self.apply_answers(result.questions, padded)
            value_map = merge_value_maps(value_map, applied)
            asked.extend(result.questions)
            answers.extend(padded)

            if stop_after_batch is not None and stop_after_batch(asked, answers, value_map):
                value_map = dump_unasked(value_map, result.pending)
                discovered = _extend_unique(discovered, result.pending)
                break
            if not applied.constraints:
                value_map = dump_unasked(value_map, result.pending)
                discovered = _extend_unique(discovered, result.pending)
                break
            if turn == max_turns:
                value_map = dump_unasked(value_map, result.pending)
                discovered = _extend_unique(discovered, result.pending)
                break

        return ElicitationSession(
            value_map=value_map,
            questions=asked,
            answers=answers,
            all_questions=discovered or asked,
            turns=turns,
        )

    async def _dedup_questions(
        self,
        questions: list[ClarifyingQuestion],
        *,
        dedup_model: str | None,
    ) -> list[ClarifyingQuestion]:
        """Сначала точные дубли, затем дешёвая модель сливает перефразы."""
        unique = _unique_by_text(questions)
        if len(unique) < 2 or not dedup_model:
            return unique
        numbered = "\n".join(f"{i}. {q.question}" for i, q in enumerate(unique))
        try:
            data = await self.provider.ask_json(
                dedup_model,
                DEDUP_PROMPT.format(numbered=numbered),
                cache_key=make_cache_key("dedup", numbered),
            )
            parsed = parse_stage(DedupVote, data, stage="dedup")
            if parsed.value is None:
                return unique
            groups = parsed.value.groups
            merged: list[ClarifyingQuestion] = []
            seen: set[int] = set()
            for group in groups:
                if not isinstance(group, list) or not group:
                    continue
                indices = [int(i) for i in group if isinstance(i, int) and 0 <= i < len(unique)]
                if not indices or any(i in seen for i in indices):
                    continue
                seen.update(indices)
                merged.append(unique[indices[0]])
            for i, q in enumerate(unique):
                if i not in seen:
                    merged.append(q)
            return merged or unique
        except (TypeError, ValueError, KeyError, RuntimeError):
            return unique

    @staticmethod
    def apply_answers(questions: list[ClarifyingQuestion], answers: list[str]) -> ValueMap:
        """Отвеченные -> constraints; неотвеченные -> open_ambiguities (round-7 §4).

        Цифра 1..n мапится на options[i-1]. Мусор, пустая строка, URL и «этот
        проект» на grounding-вопросе — не constraint.
        """
        constraints, open_ambiguities = [], []
        for q, a in zip(questions, answers, strict=False):
            normalized = normalize_answer(q, a)
            if normalized is not None and not (
                is_grounding_question(q.question) and not is_grounded_answer(normalized)
            ):
                constraints.append(f"Q: {q.question} A: {normalized}")
            else:
                open_ambiguities.append(q.question)
        for q in questions[len(answers) :]:
            open_ambiguities.append(q.question)
        return ValueMap(constraints=constraints, open_ambiguities=open_ambiguities)


def merge_value_maps(base: ValueMap, extra: ValueMap) -> ValueMap:
    """Склеить constraints/open_ambiguities; отвеченное не остаётся открытым."""
    constraints = list(base.constraints)
    for item in extra.constraints:
        if item not in constraints:
            constraints.append(item)
    open_ambiguities: list[str] = []
    for item in (*base.open_ambiguities, *extra.open_ambiguities):
        if item in open_ambiguities:
            continue
        if any(c.startswith(f"Q: {item} A:") for c in constraints):
            continue
        open_ambiguities.append(item)
    assumptions = list(base.assumptions)
    for item in extra.assumptions:
        if item not in assumptions:
            assumptions.append(item)
    return ValueMap(
        goal=base.goal or extra.goal,
        success_criteria=list(base.success_criteria or extra.success_criteria),
        constraints=constraints,
        anti_goals=list(base.anti_goals or extra.anti_goals),
        assumptions=assumptions,
        open_ambiguities=open_ambiguities,
    )


def dump_unasked(value_map: ValueMap, questions: list[ClarifyingQuestion]) -> ValueMap:
    """Незаданные вопросы — в open_ambiguities, не в assumptions."""
    return merge_value_maps(
        value_map,
        ValueMap(open_ambiguities=[q.question for q in questions]),
    )


def normalize_answer(question: ClarifyingQuestion, raw: str) -> str | None:
    """None = не отвечено. Цифра 1..n -> текст опции; одна подстрока опции — ок."""
    text = raw.strip()
    if not text:
        return None
    options = question.options
    if not options:
        return text
    if text.isdigit():
        idx = int(text)
        if 1 <= idx <= len(options):
            return options[idx - 1]
        return None
    folded = text.casefold()
    exact = [opt for opt in options if opt.casefold() == folded]
    if len(exact) == 1:
        return exact[0]
    contained = [opt for opt in options if opt.casefold() in folded]
    if len(contained) == 1:
        return contained[0]
    return None


def is_grounding_question(text: str) -> bool:
    """Вопрос про объект оценки, а не про SLO/бюджет."""
    return bool(_GROUNDING_QUESTION.search(text))


def is_grounded_answer(raw: str) -> bool:
    """Пустое, URL и дейксис («этот проект») объекта не задают."""
    text = raw.strip()
    if not text:
        return False
    if _URL.search(text):
        return False
    return not (_DEIXIS.search(text) and len(text) < 80)


def question_needs_artifact(question: str) -> bool:
    """Вопрос ссылается на внешний объект, которого у совета нет."""
    return bool(_NEEDS_ARTIFACT.search(question))


def grounding_need(
    question: str,
    questions: list[ClarifyingQuestion],
    answers: list[str],
    context: str,
) -> str | None:
    """Что именно нужно предоставить, или None если объект задан."""
    if context.strip():
        return None
    for q, raw in zip(questions, answers, strict=False):
        if is_grounding_question(q.question) and not is_grounded_answer(raw or ""):
            return (
                "the artifact under evaluation (paste the source or pass --context; "
                "a URL the council cannot fetch is not enough)"
            )
    unanswered = questions[len(answers) :]
    if any(is_grounding_question(q.question) for q in unanswered):
        return (
            "the artifact under evaluation (paste the source or pass --context; "
            "a URL the council cannot fetch is not enough)"
        )
    if question_needs_artifact(question):
        grounded = any(
            is_grounding_question(q.question) and is_grounded_answer(a)
            for q, a in zip(questions, answers, strict=False)
        )
        if not grounded:
            return (
                "the artifact under evaluation (source text or --context files, "
                "not a URL the council cannot fetch)"
            )
    return None


def _known_block(known: ValueMap, already_asked: list[str]) -> str:
    parts: list[str] = []
    if (
        known.constraints
        or known.goal
        or known.success_criteria
        or known.anti_goals
        or known.open_ambiguities
    ):
        parts.append(known.as_prompt_block())
    if already_asked:
        listed = "\n".join(f"- {q}" for q in already_asked)
        parts.append(f"Already asked (do not repeat):\n{listed}")
    if not parts:
        return ""
    return "\n".join(parts) + "\n"


def _qkey(text: str) -> str:
    return " ".join(text.casefold().split())


def _prefer_grounding(questions: list[ClarifyingQuestion]) -> list[ClarifyingQuestion]:
    """Grounding-вопрос первым в пачке — иначе IC может промахнуться."""
    head = [q for q in questions if is_grounding_question(q.question)]
    tail = [q for q in questions if not is_grounding_question(q.question)]
    return head + tail


def _extend_unique(
    existing: list[ClarifyingQuestion],
    incoming: list[ClarifyingQuestion],
) -> list[ClarifyingQuestion]:
    seen = {_qkey(q.question) for q in existing}
    out = list(existing)
    for q in incoming:
        key = _qkey(q.question)
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
    return out


def _as_question(payload: dict) -> ClarifyingQuestion | None:
    """candidate_question, иначе текст ambiguity — пустое отбрасываем."""
    text = str(payload.get("candidate_question") or payload.get("ambiguity") or "").strip()
    if not text:
        return None
    options = payload.get("options") or []
    if not isinstance(options, list):
        options = []
    return ClarifyingQuestion(
        question=text,
        why_it_matters=str(payload.get("why_it_matters") or ""),
        options=[str(o) for o in options],
    )


def _unique_by_text(questions: list[ClarifyingQuestion]) -> list[ClarifyingQuestion]:
    seen: set[str] = set()
    out: list[ClarifyingQuestion] = []
    for q in questions:
        key = _qkey(q.question)
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
    return out
