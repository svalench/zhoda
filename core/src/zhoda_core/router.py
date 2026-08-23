"""Stage -1: protocol router.

Round-3 §4: LLM self-reported confidence is uncalibrated by definition — a
model returns 0.85–0.95 almost always, and a fail-safe on it never fires.
So confidence here is INTER-MODEL AGREEMENT, not self-assessment: two
different models classify the question; agreement = confident, disagreement
= escalate to the more thorough protocol. The router finally follows the
project's own philosophy.
"""

import asyncio

from pydantic import BaseModel

from .models import Protocol, TaskClass
from .providers.openrouter import OpenRouterProvider

PROTOCOL_BY_CLASS: dict[TaskClass, Protocol] = {
    TaskClass.FACTUAL_LOOKUP: Protocol.VOTE,
    TaskClass.REASONING: Protocol.DEBATE,
    TaskClass.DECISION: Protocol.DEBATE,
    TaskClass.CODE_REVIEW: Protocol.RED_TEAM,
    TaskClass.CREATIVE: Protocol.VOTE,
}

# Fail-safe landing protocol when classifiers disagree: debate is the
# thorough default. There is no downward path.
FALLBACK_PROTOCOL = Protocol.DEBATE


class RouteDecision(BaseModel):
    task_class: TaskClass
    protocol: Protocol
    confidence: float  # 1.0 = classifiers agree, 0.0 = disagree (escalated)
    overridden: bool = False


class ProtocolRouter:
    def __init__(self, provider: OpenRouterProvider, classifiers: tuple[str, str]) -> None:
        self.provider = provider
        self.classifiers = classifiers  # two DIFFERENT models

    async def route(self, question: str, force: Protocol | None = None) -> RouteDecision:
        if force is not None:
            return RouteDecision(
                task_class=TaskClass.DECISION, protocol=force,
                confidence=1.0, overridden=True,
            )
        first, second = await asyncio.gather(
            self._classify(question, self.classifiers[0]),
            self._classify(question, self.classifiers[1]),
        )
        if first == second:
            return RouteDecision(
                task_class=first, protocol=PROTOCOL_BY_CLASS[first], confidence=1.0,
            )
        return RouteDecision(
            task_class=TaskClass.DECISION, protocol=FALLBACK_PROTOCOL, confidence=0.0,
        )

    async def _classify(self, question: str, model: str) -> TaskClass:
        """Single cheap model call, strict JSON: {task_class}. No confidence
        self-report — agreement between two classifiers is the signal."""
        raise NotImplementedError  # TODO(mvp)
