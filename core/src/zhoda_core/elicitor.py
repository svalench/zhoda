"""Stage 0: smart elicitation.

Users hate questionnaires before answers (critique §4): questions are asked
ONLY when the ambiguity score clears the threshold; otherwise the council
proceeds and marks its assumptions in the verdict. Questions ship with
one-tap options.
"""

from pydantic import BaseModel, Field

from .models import ValueMap
from .providers.openrouter import OpenRouterProvider


class ClarifyingQuestion(BaseModel):
    question: str
    why_it_matters: str
    options: list[str] = Field(default_factory=list)  # one-tap answers


class ElicitationResult(BaseModel):
    ambiguity_score: float
    questions: list[ClarifyingQuestion] = Field(default_factory=list)
    value_map: ValueMap = Field(default_factory=ValueMap)


class Elicitor:
    def __init__(self, provider: OpenRouterProvider, ambiguity_threshold: float = 0.6) -> None:
        self.provider = provider
        self.ambiguity_threshold = ambiguity_threshold  # TODO(calibrate): bench

    async def elicit(self, question: str, mode: str = "smart") -> ElicitationResult:
        """mode: smart | no-clarify | auto-clarify.

        smart: ask only if ambiguity_score >= threshold, else assumptions.
        auto-clarify: never ask; generate plausible defaults, mark them.
        no-clarify: skip the stage entirely.
        """
        raise NotImplementedError  # TODO(mvp)
