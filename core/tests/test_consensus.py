"""classify: одна primary rec → UNANIMOUS, даже если тезисы разные."""

import pytest

from zhoda_core.consensus import ConsensusChecker
from zhoda_core.factions import Faction
from zhoda_core.judges import Judges
from zhoda_core.models import ConsensusStrength, Position


class StubProvider:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    async def ask_json(self, model: str, prompt: str, **kwargs: object) -> dict:
        self.prompts.append(prompt)
        return self.payload


def _faction(name: str, thesis: str, answer: str) -> Faction:
    return Faction(
        name=name,
        members=[name],
        platform=Position(model=name, thesis=thesis, answer=answer),
    )


@pytest.mark.asyncio
async def test_classify_unanimous_on_same_primary_rec() -> None:
    provider = StubProvider({"all_agree": True})
    checker = ConsensusChecker(provider)  # type: ignore[arg-type]
    strength = await checker.classify(
        [
            _faction("A", "Use Apache Kafka", "Kafka as the event log"),
            _faction("B", "Use MSK", "managed Kafka, same log"),
            _faction("C", "Use Kafka then Cassandra", "Kafka first, Cassandra for queries"),
        ],
        judges=Judges(("j1", "j2"), {}),
    )
    assert strength is ConsensusStrength.UNANIMOUS
    assert "answer:" in provider.prompts[0]
    assert "Use MSK" in provider.prompts[0]
    assert "recommended actions" in provider.prompts[0]
