"""Stage 0: smart elicitation.

Questions are asked ONLY when the ambiguity score clears the threshold
AND mode is smart. auto-clarify and below-threshold never invent facts:
unasked items land in open_ambiguities, never in assumptions. The score is
inter-model agreement, not self-report. Round-6 §4: the loop is closed via
the on_questions callback; without it, questions degrade to open_ambiguities.
Round-7 §4: apply_answers keeps EVERYTHING — answered questions become
constraints, unanswered ones become open_ambiguities (nothing is dropped).
Round-12: leftover after top-3 also stays in open_ambiguities.
"""

from __future__ import annotations

import asyncio
import re

from pydantic import BaseModel, Field

from .models import ValueMap
from .providers.openrouter import OpenRouterProvider, make_cache_key

ELICIT_PROMPT = """You are a council member. Do NOT answer the question.
List what is ambiguous or underspecified — things whose clarification would
change the answer.

Question: {question}

Context:
{context}

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
    all_questions: list[ClarifyingQuestion] = Field(default_factory=list)  # grounding
    value_map: ValueMap = Field(default_factory=ValueMap)


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
    ) -> ElicitationResult:
        if mode == "no-clarify":
            return ElicitationResult(ambiguity_score=0.0)

        context_block = context.strip() or "(none)"
        results = await asyncio.gather(
            *(
                self.provider.ask_json(
                    m,
                    ELICIT_PROMPT.format(question=question, context=context_block),
                    cache_key=make_cache_key("elic", m, question, context_block),
                )
                for m in council
            ),
            return_exceptions=True,
        )
        payloads = [r for r in results if isinstance(r, dict)]
        if not payloads:
            raise RuntimeError("all council models failed at elicitation")

        flagged = [p for p in payloads if p.get("ambiguities")]
        score = len(flagged) / len(payloads)

        ambiguities = [a for p in flagged for a in p["ambiguities"]]
        questions = [q for a in ambiguities if (q := _as_question(a)) is not None]
        questions = await self._dedup_questions(questions, dedup_model=dedup_model)
        # auto-clarify / ниже порога: не спрашиваем и не выдаём вопросы за факты
        if mode == "auto-clarify" or score < self.ambiguity_threshold:
            return ElicitationResult(
                ambiguity_score=score,
                questions=[],
                all_questions=questions,
                value_map=ValueMap(open_ambiguities=[q.question for q in questions]),
            )
        leftover = questions[3:]
        return ElicitationResult(
            ambiguity_score=score,
            questions=questions[:3],
            all_questions=questions,
            value_map=ValueMap(open_ambiguities=[q.question for q in leftover]),
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
            )
            groups = data.get("groups")
            if not isinstance(groups, list):
                return unique
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
        key = " ".join(q.question.casefold().split())
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
    return out
