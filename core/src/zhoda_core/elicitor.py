"""Stage 0: smart elicitation.

Questions are asked ONLY when the ambiguity score clears the threshold;
otherwise the council proceeds with marked assumptions (round-2 §4). The
score is inter-model agreement, not self-report (round-3 §4). Round-5 §3:
the loop is CLOSED — engine accepts answers via on_questions callback and
fills the ValueMap; unanswered questions degrade to marked assumptions.
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

    async def elicit(self, question: str, council: list[str], mode: str = "smart") -> ElicitationResult:
        if mode == "no-clarify":
            return ElicitationResult(ambiguity_score=0.0)

        results = await asyncio.gather(
            *(self.provider.ask_json(m, ELICIT_PROMPT.format(question=question),
                                     cache_key=make_cache_key("elic", m, question))
              for m in council),
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
        """Close the loop (round-5 §3): user answers become constraints."""
        return ValueMap(
            constraints=[
                f"Q: {q.question} A: {a}"
                for q, a in zip(questions, answers, strict=False)
                if a.strip()
            ],
        )
