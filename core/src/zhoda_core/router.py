"""Stage -1: protocol router.

The router is the most 'single-model' component of the system, so it fails
SAFE: low confidence escalates to the MORE thorough protocol, never the
cheaper one (critique §1). Threshold is a magic constant until calibrated
on bench data.
"""

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

# Escalation only goes UP in thoroughness. There is no downward path.
ESCALATE_UP: dict[Protocol, Protocol] = {
    Protocol.VOTE: Protocol.DEBATE,
    Protocol.DEBATE: Protocol.DEBATE,
    Protocol.RED_TEAM: Protocol.RED_TEAM,
}


class RouteDecision(BaseModel):
    task_class: TaskClass
    protocol: Protocol
    confidence: float
    overridden: bool = False


class ProtocolRouter:
    def __init__(self, provider: OpenRouterProvider, confidence_threshold: float = 0.7) -> None:
        self.provider = provider
        self.confidence_threshold = confidence_threshold  # TODO(calibrate): bench

    async def route(self, question: str, force: Protocol | None = None) -> RouteDecision:
        if force is not None:
            return RouteDecision(
                task_class=TaskClass.DECISION, protocol=force,
                confidence=1.0, overridden=True,
            )
        task_class, confidence = await self._classify(question)
        protocol = PROTOCOL_BY_CLASS[task_class]
        if confidence < self.confidence_threshold:
            protocol = ESCALATE_UP[protocol]
        return RouteDecision(task_class=task_class, protocol=protocol, confidence=confidence)

    async def _classify(self, question: str) -> tuple[TaskClass, float]:
        """Single cheap model call, strict JSON: {task_class, confidence}.

        Mixed classes (decision+factual — most real questions) must return
        the more thorough class with lowered confidence, letting the
        threshold push them to debate.
        """
        raise NotImplementedError  # TODO(mvp)
