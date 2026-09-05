"""Values, fixed in rounds 10-11: three evidence labels, honest
paths_rejected (minority + accepted weaknesses of the winner, on zhoda only),
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
from zhoda_core.verdict import SYNTHETIC_LABEL


def test_three_evidence_labels() -> None:
    """Round-10 §1: a model-named URL is UNVERIFIED, not sourced."""
    assert Claim(claim="x", evidence_url=None).label == "assumption"
    assert Claim(claim="x", evidence_url="https://h").label == "unverified_claim"
    assert (
        Claim(claim="x", evidence_url="https://h", verified=True).label == "sourced"
    )
    assert Claim(claim="x", evidence_url="null").evidence_url is None
    assert Claim(claim="x", evidence_url="null").label == "assumption"
    assert Claim(claim="x", evidence_url="None").evidence_url is None
    assert Claim(claim="x", evidence_url="").evidence_url is None


def test_paths_rejected_only_by_a_reached_consensus() -> None:
    """Round-10 §2: at split/deadlock nothing was rejected — an unresolved
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
    assert collect_rejected_paths(factions, [], zhoda_reached=False) == []
    paths = collect_rejected_paths(factions, [], zhoda_reached=True)
    assert len(paths) == 1 and "Kafka" in paths[0].path
    assert paths[0].why  # every rejected path has a WHY


def test_b7_conditional_same_action_is_not_rejected() -> None:
    """«PostgreSQL only if audit» — условие меньшинства, не отвергнутый pick."""
    from zhoda_core.actions import attach_action, option_catalog

    question = "PostgreSQL or Kafka for a 50k RPS ledger?"
    catalog = option_catalog(question)
    leading = Faction(
        name="Pragmatists",
        members=["A", "C"],
        platform=Position(
            model="A",
            thesis="Use PostgreSQL",
            answer="PostgreSQL",
            action=attach_action("Use PostgreSQL", "PostgreSQL", catalog),
        ),
    )
    conditional = Faction(
        name="Auditors",
        members=["B"],
        platform=Position(
            model="B",
            thesis="Use PostgreSQL only if audit passes",
            answer="PG gated on audit",
            action=attach_action(
                "Use PostgreSQL only if audit passes",
                "PG gated on audit",
                catalog,
            ),
        ),
    )
    assert collect_rejected_paths(
        [leading, conditional], [], zhoda_reached=True, question=question,
    ) == []
    kafka = Faction(
        name="Throughputists",
        members=["D"],
        platform=Position(
            model="D",
            thesis="Use Kafka",
            answer="Kafka",
            action=attach_action("Use Kafka", "Kafka", catalog),
        ),
    )
    paths = collect_rejected_paths(
        [leading, kafka], [], zhoda_reached=True, question=question,
    )
    assert len(paths) == 1 and "Kafka" in paths[0].path


def test_paths_rejected_include_accepted_weaknesses_of_the_winner() -> None:
    """Round-11 §1: an objection that stayed open against the WINNER is an
    accepted weakness — the unaddressed version of the chosen path was
    rejected. Counted only on zhoda."""
    leading = Faction(
        name="Pragmatists", members=["A", "C"],
        platform=Position(model="A", thesis="Use PostgreSQL", answer="..."),
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
    # no zhoda -> nothing rejected, not even the winner's open weakness
    assert collect_rejected_paths([leading], objections, zhoda_reached=False) == []
    paths = collect_rejected_paths([leading], objections, zhoda_reached=True)
    assert len(paths) == 1 and "write-scaling" in paths[0].path
    assert "accepted weakness" in paths[0].why


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
        Critique(
            id="2", author_faction="Throughputists", target_faction="Pragmatists",
            flaw_type=FlawType.FACTUAL,
            claim="see the write amplification paper",
            evidence_url="https://example.com/paper",
            status=ObjectionStatus.OPEN,
        ),
    ]
    tree = build_decision_tree([faction], objections, [], "Use PostgreSQL")
    assumption_node = tree.children[0].children[0]
    url_node = tree.children[0].children[1]
    assert assumption_node.detail["resolution"] == "addressed by platform revision"
    assert assumption_node.detail["evidence"] == "assumption"
    assert url_node.detail["evidence"] == "unverified_claim"
    assert tree.children[0].detail["claims"][0]["label"] == "unverified_claim"
    assert tree.children[0].detail["synthetic"] is False
    assert "note" not in tree.children[0].detail


def test_synthetic_faction_is_labeled_on_the_tree() -> None:
    faction = Faction(
        name="Pivot Advocates",
        members=["devils_advocate"],
        synthetic=True,
        platform=Position(model="devils_advocate", thesis="Pivot", answer="..."),
    )
    tree = build_decision_tree([faction], [], [], "Continue")
    assert tree.children[0].detail["synthetic"] is True
    assert tree.children[0].detail["note"] == SYNTHETIC_LABEL
