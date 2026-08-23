"""Stage 0: smart elicitation.

Questions are asked ONLY when the ambiguity score clears the threshold;
otherwise the council proceeds with marked assumptions. The score is
inter-model agreement, not self-report. Round-6 §4: the loop is closed via
the on_questions callback; without it, questions degrade to open_ambiguities.
Round-7 §4: apply_answers keeps EVERYTHING — answered questions become
constraints, unanswered ones become open_ambiguities (nothing is dropped).
"""

import asyncio

from pydantic import BaseModel, Field

from .models import ValueMap
from .providers.openrouter import OpenRouterProvider, make_cache_key

ELICIT_PROMPT = """You are a council member. Do NOT answer the question.
List what is ambiguous or underspecified — things whose clarification would
change the answer.

Question: {question}

Respond with ONLY valid JSON:
{{"ambiguities": [{{"ambiguity": "...", "why_it_matters": "...",
  "candidate_question": "...", "options": ["one-tap answer", "..."]}}]}}
If nothing is ambiguous, return {{"ambiguities": []}}."""


class ClarifyingQuestion(BaseModel):
    question: str
    why_it_matters: str
    options: list[str] = Field(default_factory=list)


class ElicitationResult(BaseModel):
    ambiguity_score: float
    questions: list[ClarifyingQuestion] = Field(default_factory=list)
    value_map: ValueMap = Field(default_factory=ValueMap)


class Elicitor:
    def __init__(self, provider: OpenRouterProvider, ambiguity_threshold: float = 0.6) -> None:
        self.provider = provider
        self.ambiguity_threshold = ambiguity_threshold  # TODO(calibrate): bench

    async def elicit(
        self, question: str, council: list[str], mode: str = "smart"
    ) -> ElicitationResult:
        if mode == "no-clarify":
            return ElicitationResult(ambiguity_score=0.0)

        results = await asyncio.gather(
            *(
                self.provider.ask_json(
                    m,
                    ELICIT_PROMPT.format(question=question),
                    cache_key=make_cache_key("elic", m, question),
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
        if mode == "auto-clarify" or score < self.ambiguity_threshold:
            return ElicitationResult(
                ambiguity_score=score,
                value_map=ValueMap(assumptions=[a["ambiguity"] for a in ambiguities]),
            )
        questions = [
            ClarifyingQuestion(
                question=a["candidate_question"],
                why_it_matters=a["why_it_matters"],
                options=a.get("options", []),
            )
            for a in ambiguities[:3]
        ]
        return ElicitationResult(ambiguity_score=score, questions=questions)

    @staticmethod
    def apply_answers(questions: list[ClarifyingQuestion], answers: list[str]) -> ValueMap:
        """Отвеченные -> constraints; неотвеченные -> open_ambiguities (round-7 §4).

        Цифра 1..n мапится на options[i-1]. Мусор и пустая строка — не constraint.
        """
        constraints, open_ambiguities = [], []
        for q, a in zip(questions, answers, strict=False):
            normalized = normalize_answer(q, a)
            if normalized is not None:
                constraints.append(f"Q: {q.question} A: {normalized}")
            else:
                open_ambiguities.append(q.question)
        for q in questions[len(answers) :]:
            open_ambiguities.append(q.question)
        return ValueMap(constraints=constraints, open_ambiguities=open_ambiguities)


def normalize_answer(question: ClarifyingQuestion, raw: str) -> str | None:
    """None = не отвечено. Цифра 1..n -> текст опции; мусор при опциях -> None."""
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
    if len(contained) >= 2:
        return None
    return None
