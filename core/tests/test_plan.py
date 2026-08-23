"""Values №1–№3: evidence discipline, plan contract, dead-ends metric."""

from zhoda_core.factions import Faction
from zhoda_core.models import (
    Claim,
    Critique,
    FlawType,
    ObjectionStatus,
    Position,
)
from zhoda_core.plan import collect_rejected_paths
from zhoda_core.tree import build_decision_tree


def test_unsourced_claim_is_labeled_opinion() -> None:
    """Values №1: without a source it's an assumption, and renders say so."""
    assert not Claim(claim="PostgreSQL handles 50k RPS", evidence_url=None).is_sourced
    assert Claim(claim="benchmark", evidence_url="https://example.com/b").is_sourced


def test_rejected_paths_from_ledger_and_minority() -> None:
    """Values №3: rejected paths are collected programmatically — the minority
    platform AND open objections against the winner."""
    leading = Faction(
        name="Pragmatists", members=["A"],
        platform=Position(model="A", thesis="Use PostgreSQL", answer="..."),
    )
    minority = Faction(
        name="Throughputists", members=["B"],
        platform=Position(model="B", thesis="Use Kafka", answer="..."),
    )
    objections = [
        Critique(
            id="1", author_faction="Throughputists", target_faction="Pragmatists",
            flaw_type=FlawType.SCOPE,
            claim="no write-scaling story beyond a single node",
            specifics="partitioning and replication are unaddressed",
            status=ObjectionStatus.OPEN,
        ),
    ]
    paths = collect_rejected_paths([leading, minority], objections, leading)
    assert any("Kafka" in p.path for p in paths)           # minority path preserved
    assert any("write-scaling" in p.path for p in paths)   # open objection vs winner
    assert all(p.why for p in paths)                        # every path has a WHY


def test_decision_tree_shows_what_closed_each_objection() -> None:
    """Values №1: the tree answers 'argument -> what closed it -> who moved'."""
    faction = Faction(
        name="Pragmatists", members=["A"],
        platform=Position(
            model="A", thesis="Use PostgreSQL", answer="...",
            claims=[Claim(claim="battle-tested", evidence_url=None)],
        ),
    )
    objections = [
        Critique(
            id="1", author_faction="Throughputists", target_faction="Pragmatists",
            flaw_type=FlawType.FACTUAL,
            claim="PostgreSQL handles 50k RPS writes on one node",
            status=ObjectionStatus.SUPERSEDED,
        ),
    ]
    tree = build_decision_tree([faction], objections, [], "Use PostgreSQL")
    objection_node = tree.children[0].children[0]
    assert objection_node.detail["resolution"] == "addressed by platform revision"
    assert tree.children[0].detail["claims"][0]["label"] == "assumption"
