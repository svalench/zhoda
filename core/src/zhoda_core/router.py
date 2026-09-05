"""Stage -1: protocol router.

Confidence is INTER-MODEL AGREEMENT (round-3 §4): two DIFFERENT models
classify; agreement = confident, disagreement = escalate up. Classifiers come
from explicit config, never council order; disagreement honestly reports the
more thorough class (round-4 §3). Limitation: agreement is NOT calibrated
correctness — same-family models can be confidently wrong together.
"""

import asyncio

from pydantic import BaseModel

from .models import Protocol, TaskClass
from .providers.openrouter import OpenRouterProvider, make_cache_key
from .stage_dtos import ClassifyVote, parse_stage

PROTOCOL_BY_CLASS: dict[TaskClass, Protocol] = {
    TaskClass.FACTUAL_LOOKUP: Protocol.VOTE,
    TaskClass.REASONING: Protocol.DEBATE,
    TaskClass.DECISION: Protocol.DEBATE,
    TaskClass.CODE_REVIEW: Protocol.RED_TEAM,
    TaskClass.CREATIVE: Protocol.VOTE,
}

THOROUGHNESS: dict[Protocol, int] = {
    Protocol.VOTE: 0,
    Protocol.DEBATE: 1,
    Protocol.RED_TEAM: 2,
}

FALLBACK_PROTOCOL = Protocol.DEBATE  # disagreement always lands here

CLASSIFY_PROMPT = """Classify this user question into exactly one class:
factual_lookup | reasoning | decision | code_review | creative

Question: {question}

Respond with ONLY valid JSON: {{"task_class": "..."}}"""


class RouteDecision(BaseModel):
    task_class: TaskClass
    protocol: Protocol
    confidence: float  # 1.0 = classifiers agree, 0.0 = disagree (escalated)
    overridden: bool = False


class ProtocolRouter:
    def __init__(self, provider: OpenRouterProvider, classifiers: tuple[str, str]) -> None:
        if len(set(classifiers)) < 2:
            raise ValueError("router needs two DISTINCT classifier models")
        self.provider = provider
        self.classifiers = classifiers

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
        if first is not None and first == second:
            return RouteDecision(
                task_class=first, protocol=PROTOCOL_BY_CLASS[first], confidence=1.0,
            )
        valid = [item for item in (first, second) if item is not None]
        if len(valid) >= 2:
            thorough = max(valid, key=lambda c: THOROUGHNESS[PROTOCOL_BY_CLASS[c]])
        elif valid:
            thorough = valid[0]
        else:
            thorough = TaskClass.DECISION
        return RouteDecision(
            task_class=thorough, protocol=FALLBACK_PROTOCOL, confidence=0.0,
        )

    async def _classify(self, question: str, model: str) -> TaskClass | None:
        prompt = CLASSIFY_PROMPT.format(question=question)
        data = await self.provider.ask_json(
            model, prompt,
            cache_key=make_cache_key("route", model, question),
        )
        parsed = parse_stage(ClassifyVote, data, stage="route", prompt=prompt)
        if parsed.value is None:
            return None
        return parsed.value.task_class
