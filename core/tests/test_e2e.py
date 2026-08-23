"""End-to-end test of the deliberation loop on a SCRIPTED provider.

Proves the flagship mechanic (round-5 §1): a faction REVISES its platform
after an unclosed objection, and the verdict carries the revised thesis —
convergence by synthesis, not attrition. Deterministic via alias_seed.
"""

import json

import pytest

from zhoda_core.anonymize import make_aliases
from zhoda_core.engine import ZhodaEngine
from zhoda_core.models import Protocol
from zhoda_core.providers.openrouter import OpenRouterProvider

COUNCIL = ["m1", "m2", "m3"]
JUDGES = ("j1", "j2")  # outside the council — no conflicts

PG = "Use PostgreSQL (simple, sufficient)"
KF = "Use Kafka (built for throughput)"
PG_REVISED = "Use PostgreSQL with a partitioning plan for write scaling"


def position(thesis: str) -> str:
    return json.dumps({
        "thesis": thesis, "answer": f"Answer: {thesis}",
        "arguments": ["arg"], "falsifiability": "if load grows", "confidence": 0.7,
    })


class ScriptedProvider(OpenRouterProvider):
    """Answers from an ordered (model, pattern, response) script."""

    def __init__(self, script: list[tuple[str | None, str, object]]) -> None:
        super().__init__(api_key="test")
        self.script = script

    async def complete(self, model, prompt, **kwargs):  # noqa: ANN001, ANN202
        for want_model, pattern, response in self.script:
            if (want_model is None or want_model == model) and pattern in prompt:
                return response if isinstance(response, str) else json.dumps(response)
        raise AssertionError(f"no scripted response: model={model} prompt={prompt[:80]}")

    async def ask_json(self, model, prompt, **kwargs):  # noqa: ANN001, ANN202
        return json.loads(await self.complete(model, prompt, **kwargs))


@pytest.mark.asyncio
async def test_debate_loop_with_platform_revision() -> None:
    aliases = make_aliases(COUNCIL, seed=42)
    a1, a2 = aliases["m1"], aliases["m2"]

    script = [
        # stage 1: positions — m1/m3 for PostgreSQL, m2 for Kafka
        ("m1", "independent structured stance", position(PG)),
        ("m2", "independent structured stance", position(KF)),
        ("m3", "independent structured stance", position(PG)),
        # stage 2: pairwise — differ when Kafka on the B side
        (None, "Position B thesis: Use Kafka", {"same": False, "divergence": "simplicity vs throughput"}),
        (None, "Position A thesis:", {"same": True, "divergence": ""}),
        # round 1: critiques (leading faction speaks first member = a1)
        (None, f'You represent faction "{a1}"', {
            "target_faction": a2, "flaw_type": "factual",
            "claim": "Kafka adds operational complexity the team cannot staff",
            "specifics": ""}),
        (None, f'You represent faction "{a2}"', {
            "target_faction": a1, "flaw_type": "scope",
            "claim": "PostgreSQL at 50k RPS writes needs a partitioning plan",
            "specifics": "no write-scaling story beyond a single node"}),
        (None, "devil's advocate", {
            "target_faction": a1, "flaw_type": "logical",
            "claim": "the platform ignores read replicas as a simpler scaling path",
            "specifics": ""}),
        # rebuttals
        (None, "platform thesis: Use Kafka", "We accept the complexity point."),
        (None, "platform thesis: Use PostgreSQL", "Partitioning is a solved problem."),
        # closure votes (judge pair): complexity -> closed, partitioning -> OPEN
        (None, "operational complexity", {"closed": True}),
        (None, "partitioning plan", {"closed": False}),
        (None, "read replicas", {"closed": True}),
        # switches: leading faction members refuse to move
        ("m1", "Do you switch factions?", {"switch": False, "convinced_by": ""}),
        ("m3", "Do you switch factions?", {"switch": False, "convinced_by": ""}),
        # REVISION: leading faction updates its platform (the flagship stage)
        ("m1", "Revise your platform", {
            "thesis": PG_REVISED, "answer": f"Answer: {PG_REVISED}",
            "arguments": ["arg", "partitioning plan"], "falsifiability": "if writes explode",
            "confidence": 0.8, "changed": True,
            "change_note": "added partitioning after the scope objection"}),
        # consensus: theses still differ (Kafka faction stands) -> majority
        (None, "theses of all factions", {"all_agree": False}),
    ]

    provider = ScriptedProvider(script)
    engine = ZhodaEngine(
        provider, COUNCIL, chairman="j1", judges=JUDGES,
        stability_rounds=1, alias_seed=42, transcripts_dir="/tmp/zhoda-test",
    )
    verdict = await engine.deliberate(
        "PostgreSQL or Kafka for a 50k RPS ledger?",
        force_protocol=Protocol.DEBATE, clarify_mode="no-clarify",
    )

    assert verdict.zhoda_reached is True
    assert verdict.consensus_strength == "majority"
    assert "partitioning" in verdict.decision  # revised platform won — not attrition
    assert verdict.minority_report and "Kafka" in verdict.minority_report
    assert verdict.cost.requests > 0
    assert verdict.transcript_id
