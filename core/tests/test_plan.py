"""Values, fixed in round 10: three evidence labels, honest paths_rejected,
plan gated on zhoda."""

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


def test_three_evidence_labels() -> None:
    """Round-10 §1: a model-named URL is UNVERIFIED, not sourced."""
    assert Claim(claim="x", evidence_url=None).label == "assumption"
    assert Claim(claim="x", evidence_url="https://h").label == "unverified_claim"
    assert (
        Claim(claim="x", evidence_url="https://h", verified=True).label == "sourced"
    )


def test_paths_rejected_only_by_a_reached_consensus() -> None:
    """Round-10 §2/§3: at split/deadlock nothing was rejected — an unresolved
    dispute is not a rejection and is not counted."""
    leading = Faction(
        name="Pragmatists", members=["A", "C"],
        platform=Position(model="A", thesis="Use PostgreSQL", answer="..."),
    )
    minority = Faction(
        name="Throughputists", members=["B"],
        platform=Position(model="B", thesis="Use Kafka", answer="..."),
    )
    factions = [leading, minority]
    assert collect_rejected_paths(factions, zhoda_reached=False) == []
    paths = collect_rejected_paths(factions, zhoda_reached=True)
    assert len(paths) == 1 and "Kafka" in paths[0].path
    assert paths[0].why  # every rejected path has a WHY


def test_decision_tree_three_labels() -> None:
    """The tree shows what closed each objection — and labels evidence honestly."""
    faction = Faction(
        name="Pragmatists", members=["A"],
        platform=Position(
            model="A", thesis="Use PostgreSQL", answer="...",
            claims=[Claim(claim="battle-tested", evidence_url="https://x")],
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
    assert objection_node.detail["evidence"] == "unverified_claim"
    assert tree.children[0].detail["claims"][0]["label"] == "unverified_claim"
