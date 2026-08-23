"""UNANIMOUS без minority; deadlock — карта тезисов, не answer лидера."""

from zhoda_core.factions import Faction
from zhoda_core.models import (
    ConsensusStrength,
    CostReport,
    Position,
    Protocol,
    ValueMap,
)
from zhoda_core.verdict import VerdictBuilder


def _faction(name: str, thesis: str, answer: str) -> Faction:
    return Faction(
        name=name,
        members=[name],
        platform=Position(model=name, thesis=thesis, answer=answer),
    )


def _build(factions: list[Faction], strength: ConsensusStrength, *, zhoda: bool):
    return VerdictBuilder().build(
        factions,
        strength,
        Protocol.DEBATE,
        ValueMap(),
        zhoda_reached=zhoda,
        router_confidence=1.0,
        rounds_taken=4,
        transcript_id="t1",
        switches=[],
        cost=CostReport(),
        divergences=[],
    )


def test_unanimous_drops_minority_even_with_three_singletons() -> None:
    factions = [
        _faction("Kafkaists", "Use Kafka", "Kafka core"),
        _faction("MSK", "Use managed Kafka", "MSK is fine"),
        _faction("Loggers", "Use a commit log", "a log, e.g. Kafka"),
    ]
    verdict = _build(factions, ConsensusStrength.UNANIMOUS, zhoda=True)
    assert verdict.minority_report is None
    assert verdict.decision == "Use Kafka"


def test_deadlock_decision_lists_all_theses_not_leader_answer() -> None:
    factions = [
        _faction("Kafkaists", "Use Kafka", "ONLY Kafka raw answer"),
        _faction("Cassandra", "Use Cassandra after Kafka", "Cassandra complement"),
        _faction("MSK", "Use MSK", "managed Kafka"),
    ]
    verdict = _build(factions, ConsensusStrength.DEADLOCK, zhoda=False)
    assert verdict.decision != "ONLY Kafka raw answer"
    assert "Use Kafka" in verdict.decision
    assert "Use Cassandra after Kafka" in verdict.decision
    assert "Use MSK" in verdict.decision
    assert "No zhoda" in verdict.decision
    assert verdict.minority_report
    assert "Cassandra" in verdict.minority_report
