"""Слияние по primary rec; negation никогда не auto-merge."""

import pytest

from zhoda_core.factions import PAIRWISE_PROMPT, FactionClusterer, near_identical
from zhoda_core.judges import Judges
from zhoda_core.models import Position


class QueueProvider:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)

    async def ask_json(self, model: str, prompt: str, **kwargs: object) -> dict:
        return self.payloads.pop(0)


def _pos(model: str, thesis: str, answer: str | None = None) -> Position:
    return Position(model=model, thesis=thesis, answer=answer or f"Answer: {thesis}")


def test_pairwise_prompt_judges_recommended_actions() -> None:
    assert "recommended actions" in PAIRWISE_PROMPT


def test_negation_never_auto_merges() -> None:
    assert near_identical("use Kafka", "don't use Kafka") is False
    assert near_identical("use Kafka", "avoid Kafka") is False
    assert near_identical("use Kafka for the log", "use Kafka for the log") is True


@pytest.mark.asyncio
async def test_judge_same_merges_kafka_variants() -> None:
    provider = QueueProvider(
        [
            {"same": True, "divergence": ""},
            {
                "thesis": "Use Kafka",
                "answer": "Kafka is the core event store",
                "claims": [],
                "falsifiability": "if ordering fails",
                "confidence": 0.8,
            },
        ]
    )
    clusterer = FactionClusterer(provider)  # type: ignore[arg-type]
    factions = await clusterer.cluster(
        [
            _pos("a1", "Use Kafka"),
            _pos("a2", "Use managed Kafka (MSK)"),
        ],
        judges=Judges(("j1", "j2"), {}),
        speakers={"a1": "m1", "a2": "m2"},
    )
    assert len(factions) == 1
    assert set(factions[0].members) == {"a1", "a2"}


@pytest.mark.asyncio
async def test_judge_different_keeps_postgres_vs_kafka() -> None:
    provider = QueueProvider([{"same": False, "divergence": "store vs log"}])
    clusterer = FactionClusterer(provider)  # type: ignore[arg-type]
    factions = await clusterer.cluster(
        [
            _pos("a1", "Use PostgreSQL"),
            _pos("a2", "Use Kafka"),
        ],
        judges=Judges(("j1", "j2"), {}),
        speakers={"a1": "m1", "a2": "m2"},
    )
    assert len(factions) == 2
