"""Stage 0: smart elicitation.

Users hate questionnaires before answers (round-2 §4): questions are asked
ONLY when the ambiguity score clears the threshold; otherwise the council
proceeds and marks its assumptions in the verdict. Questions ship with
one-tap options.

Ambiguity score is NOT self-reported (round-3 §4 taught us better): it is the
share of council models that independently flagged ambiguities — inter-model
agreement again. TODO(calibrate): threshold on bench data.
"""

import asyncio

from pydantic import BaseModel, Field

from .models import ValueMap
from .providers.openrouter import OpenRouterProvider

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
                                     cache_key=f"elic:{m}:{hash(question)}")
              for m in council),
            return_exceptions=True,
        )
        payloads = [r for r in results if isinstance(r, dict)]
        if not payloads:
            raise RuntimeError("all council models failed at elicitation")

        flagged = [p for p in payloads if p.get("ambiguities")]
        score = len(flagged) / len(payloads)  # inter-model agreement, not self-report

        ambiguities = [a for p in flagged for a in p["ambiguities"]]
        if mode == "auto-clarify" or score < self.ambiguity_threshold:
            # proceed on marked assumptions instead of asking
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
            for a in ambiguities[:3]  # top-3 max, never a questionnaire
        ]
        return ElicitationResult(ambiguity_score=score, questions=questions)
