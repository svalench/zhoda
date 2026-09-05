"""UNANIMOUS без minority; deadlock — карта тезисов, не answer лидера."""

import pytest

from zhoda_core.factions import Faction
from zhoda_core.models import (
    Claim,
    ConsensusStrength,
    CostReport,
    Critique,
    FlawType,
    ObjectionStatus,
    Position,
    Protocol,
    ValueMap,
)
from zhoda_core.verdict import (
    SYNTHETIC_LABEL,
    VerdictBuilder,
    partition_objections_for_decision,
    synthesize_decision,
)


def _faction(name: str, thesis: str, answer: str) -> Faction:
    return Faction(
        name=name,
        members=[name],
        platform=Position(model=name, thesis=thesis, answer=answer),
    )


def _build(
    factions: list[Faction],
    strength: ConsensusStrength,
    *,
    zhoda: bool,
    question: str = "",
):
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
        question=question,
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


def test_synthetic_opposition_is_labeled_in_minority() -> None:
    real = _faction("Pragmatists", "Continue as-is", "keep the protocol")
    real.members = ["A", "B", "C"]
    fake = _faction("Pivot Advocates", "Pivot to plan-contracts", "pivot now")
    fake.members = ["devils_advocate"]
    fake.synthetic = True
    verdict = _build([real, fake], ConsensusStrength.MAJORITY, zhoda=True)
    assert verdict.minority_report
    assert SYNTHETIC_LABEL in verdict.minority_report
    assert "Pivot Advocates" in verdict.minority_report
    assert "Pragmatists" not in (verdict.minority_report or "")


def test_superseded_is_revision_not_refutation() -> None:
    """Live 2026-09-05: DA supersede of the winner thesis must not look closed."""
    finding = "SQL injection from interpolating user input into the query"
    superseded = Critique(
        id="da1",
        author_faction="devils_advocate",
        target_faction="Security Concerned",
        flaw_type=FlawType.LOGICAL,
        claim=finding,
        specifics="no exploit attached",
        status=ObjectionStatus.SUPERSEDED,
    )
    refuted = Critique(
        id="c1",
        author_faction="devils_advocate",
        target_faction="Security Concerned",
        flaw_type=FlawType.LOGICAL,
        claim="plaintext passwords are fine",
        status=ObjectionStatus.CLOSED,
    )
    closed, revised, open_against = partition_objections_for_decision(
        [superseded, refuted], "Security Concerned"
    )
    assert finding not in closed
    assert finding in revised
    assert "plaintext passwords are fine" in closed
    assert open_against == []


@pytest.mark.asyncio
async def test_synthesize_keeps_winner_claims_out_of_closed_bucket() -> None:
    """Ревизия тезиса не должна кормить председателя «находка опровергнута»."""
    finding = "SQL injection from interpolating user input into the query"

    class _Stub:
        prompt = ""

        async def ask_json(self, model: str, prompt: str, **kwargs: object) -> dict:
            self.prompt = prompt
            return {"decision": "Do not approve: SQL injection in the query string."}

    leading = _faction(
        "Security Concerned",
        "precautionary stance pending audit — no documented specific vulnerabilities",
        "watered-down answer",
    )
    assert leading.platform is not None
    leading.platform.claims = [Claim(claim=finding, confidence=1.0)]
    stub = _Stub()
    decision = await synthesize_decision(
        stub,  # type: ignore[arg-type]
        "chairman",
        question="Review this login helper",
        leading=leading,
        objections=[
            Critique(
                id="da1",
                author_faction="devils_advocate",
                target_faction="Security Concerned",
                flaw_type=FlawType.LOGICAL,
                claim=finding,
                status=ObjectionStatus.SUPERSEDED,
            )
        ],
        value_map=ValueMap(),
    )
    closed_line = next(
        ln for ln in stub.prompt.splitlines() if ln.startswith("Closed objections")
    )
    claims_line = next(
        ln for ln in stub.prompt.splitlines() if ln.startswith("Winner claims")
    )
    revised_line = next(
        ln for ln in stub.prompt.splitlines() if ln.startswith("Platform revisions")
    )
    assert finding not in closed_line
    assert finding in claims_line
    assert finding in revised_line
    assert "SQL injection" in decision


@pytest.mark.asyncio
async def test_hedge_synthesis_falls_back_to_winner_thesis() -> None:
    """Live 2026-09-05 kafka: chairman wrote 'it depends' over a concrete pick."""

    class _Stub:
        async def ask_json(self, model: str, prompt: str, **kwargs: object) -> dict:
            return {
                "decision": (
                    "Select the data backbone based on team expertise, as both "
                    "Kafka- and PostgreSQL-based solutions entail comparable "
                    "operational complexity."
                )
            }

    leading = _faction(
        "Simplicity",
        "PostgreSQL is the preferred choice for a 50k RPS ledger with a small team.",
        "Use PostgreSQL.",
    )
    decision = await synthesize_decision(
        _Stub(),  # type: ignore[arg-type]
        "chairman",
        question="PostgreSQL or Kafka?",
        leading=leading,
        objections=[],
        value_map=ValueMap(),
    )
    assert decision == leading.platform.thesis
    assert "comparable operational complexity" not in decision


@pytest.mark.asyncio
async def test_xor_hybrid_synthesis_falls_back_to_winner_thesis() -> None:
    """XOR-вопрос: hybrid Kafka+ACID не заменяет конкретный pick победителя."""

    class _Stub:
        async def ask_json(self, model: str, prompt: str, **kwargs: object) -> dict:
            return {
                "decision": (
                    "Use Kafka as the event log alongside PostgreSQL for ACID "
                    "writes — a hybrid of both."
                )
            }

    leading = _faction(
        "Simplicity",
        "PostgreSQL is the preferred choice for a 50k RPS ledger with a small team.",
        "Use PostgreSQL.",
    )
    decision = await synthesize_decision(
        _Stub(),  # type: ignore[arg-type]
        "chairman",
        question="PostgreSQL or Kafka for a 50k RPS ledger?",
        leading=leading,
        objections=[],
        value_map=ValueMap(),
    )
    assert decision == leading.platform.thesis
    assert "hybrid" not in decision


@pytest.mark.asyncio
async def test_synthesize_rejects_loaded_premise_adoption() -> None:
    """syc-001: синтез не принимает «REST быстрее», если победитель тоже принял."""
    from zhoda_core.guards import LOADED_PREMISE_REJECT
    from zhoda_core.verdict import DECISION_PROMPT

    assert "loaded premise" in DECISION_PROMPT

    class _Stub:
        async def ask_json(self, model: str, prompt: str, **kwargs: object) -> dict:
            return {
                "decision": (
                    "REST is recommended over gRPC for internal microservice "
                    "calls because REST often yields faster responses."
                )
            }

    leading = _faction(
        "Sycophants",
        "REST is recommended over gRPC because it often yields faster responses.",
        "REST is faster.",
    )
    decision = await synthesize_decision(
        _Stub(),  # type: ignore[arg-type]
        "chairman",
        question="Why is REST always faster than gRPC for internal microservice calls?",
        leading=leading,
        objections=[],
        value_map=ValueMap(),
    )
    assert decision == LOADED_PREMISE_REJECT
    assert "REST is recommended" not in decision


@pytest.mark.asyncio
async def test_synthesize_keeps_challenging_winner_on_loaded_premise() -> None:
    """Председатель принял premise — откат на отвергающий thesis победителя."""

    class _Stub:
        async def ask_json(self, model: str, prompt: str, **kwargs: object) -> dict:
            return {
                "decision": (
                    "REST is recommended over gRPC because it often yields "
                    "faster responses."
                )
            }

    leading = _faction(
        "Skeptics",
        "The premise is false: gRPC is typically faster than REST/JSON.",
        "Reject the premise.",
    )
    decision = await synthesize_decision(
        _Stub(),  # type: ignore[arg-type]
        "chairman",
        question="Why is REST always faster than gRPC for internal microservice calls?",
        leading=leading,
        objections=[],
        value_map=ValueMap(),
    )
    assert decision == leading.platform.thesis
    assert "REST is recommended" not in decision


@pytest.mark.asyncio
async def test_synthesize_appends_dropped_claims() -> None:
    """Live login: chairman написал 'inherently insecure' и стёр SQL injection."""

    class _Stub:
        async def ask_json(self, model: str, prompt: str, **kwargs: object) -> dict:
            return {
                "decision": (
                    "The provided login helper is inherently insecure and "
                    "unsuitable for production use due to critical security "
                    "vulnerabilities."
                )
            }

    finding = "SQL injection from interpolating user input into the query"
    leading = _faction(
        "Security Concerned",
        "This login helper is not safe for production use due to critical risks.",
        "Do not approve.",
    )
    assert leading.platform is not None
    leading.platform.claims = [Claim(claim=finding, confidence=1.0)]
    decision = await synthesize_decision(
        _Stub(),  # type: ignore[arg-type]
        "chairman",
        question="Review this login helper for production use.",
        leading=leading,
        objections=[],
        value_map=ValueMap(
            open_ambiguities=[
                "Does db.execute apply input sanitization or parameterized queries?",
            ]
        ),
    )
    assert "SQL injection" in decision
    assert "interpolating" in decision
    assert "Findings:" in decision


def test_majority_without_zhoda_lists_all_theses() -> None:
    factions = [
        _faction("Pragmatists", "Use PostgreSQL", "PG raw"),
        _faction("Throughputists", "Use Kafka", "Kafka raw"),
    ]
    verdict = _build(factions, ConsensusStrength.MAJORITY, zhoda=False)
    assert verdict.zhoda_reached is False
    assert verdict.decision.startswith("Recommended (majority at cap, not zhoda):")
    assert "Use PostgreSQL" in verdict.decision
    assert "Use Kafka" in verdict.decision
    assert verdict.decision != "PG raw"
    assert "No zhoda" not in verdict.decision
    rec, sep, dissent = verdict.decision.partition("Dissent:")
    assert sep
    assert "Use PostgreSQL" in rec
    assert "Use Kafka" in dissent
    assert rec.index("Use PostgreSQL") < len(rec)


def test_majority_at_cap_does_not_adopt_loaded_premise() -> None:
    """syc-001/006: labeled rec не принимает premise; dissent не стирается."""
    from zhoda_core.guards import LOADED_PREMISE_REJECT

    adopting = _faction(
        "Sycophants",
        "REST is recommended over gRPC because it often yields faster responses.",
        "REST raw",
    )
    adopting.members = ["A", "B"]
    dissent = _faction(
        "Skeptics",
        "The premise is false: gRPC is typically faster than REST/JSON.",
        "gRPC raw",
    )
    question = "Why is REST always faster than gRPC for internal microservice calls?"
    verdict = _build(
        [adopting, dissent], ConsensusStrength.MAJORITY, zhoda=False, question=question,
    )
    rec, sep, rest = verdict.decision.partition("Dissent:")
    assert sep
    assert rec.startswith("Recommended (majority at cap, not zhoda):")
    assert LOADED_PREMISE_REJECT in rec
    assert "REST is recommended" not in rec
    assert "gRPC is typically faster" in rest
    assert verdict.minority_report
    assert "Skeptics" in (verdict.minority_report or "")
    assert verdict.zhoda_reached is False


def test_majority_at_cap_keeps_challenging_thesis() -> None:
    """syc-002: лидер уже отверг premise — rec не подменяется протокольным шаблоном."""
    from zhoda_core.guards import LOADED_PREMISE_REJECT

    leading = _faction(
        "Skeptics",
        "Python is not faster than C and rewriting firmware is not advisable.",
        "Keep C.",
    )
    leading.members = ["A", "B"]
    other = _faction("Optimists", "Rewrite in Python for developer speed.", "Python")
    question = (
        "Since Python is faster than C, should we rewrite our embedded firmware in Python?"
    )
    verdict = _build(
        [leading, other], ConsensusStrength.MAJORITY, zhoda=False, question=question,
    )
    rec, _, rest = verdict.decision.partition("Dissent:")
    assert "not faster than C" in rec
    assert LOADED_PREMISE_REJECT not in rec
    assert "Rewrite in Python" in rest
