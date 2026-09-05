"""Unit tests for the objection ledger gates — no provider calls."""

import pytest

from zhoda_core.debate import DebateEngine
from zhoda_core.models import Critique, FactionSwitch, FlawType, ObjectionStatus


def make_engine(**kwargs) -> DebateEngine:
    return DebateEngine(provider=None, **kwargs)  # gates don't touch the provider


def make_critique(**kwargs) -> Critique:
    base = {
        "author_faction": "Throughputists",
        "target_faction": "Pragmatists",
        "flaw_type": FlawType.FACTUAL,
        "claim": "PostgreSQL handles 50k RPS writes on a single node",
    }
    return Critique(**(base | kwargs))


def make_switch(**kwargs) -> FactionSwitch:
    base = {
        "model": "Response A",
        "from_faction": "Pragmatists",
        "to_faction": "Throughputists",  # the objection's author
        "convinced_by": "the write-scaling argument",
    }
    return FactionSwitch(**(base | kwargs))


def test_vague_scope_critique_is_rejected() -> None:
    engine = make_engine()
    with pytest.raises(ValueError):
        engine.register_critique(make_critique(
            flaw_type=FlawType.SCOPE,
            claim="you did not consider important aspects of scalability and more",
            specifics="",
        ))


def test_close_requires_rebuttal_from_target_faction() -> None:
    engine = make_engine()
    critique = engine.register_critique(make_critique())
    assert not engine.close_objection(critique.id, "no", rebuttal_by="Opponents")
    assert critique.status == ObjectionStatus.OPEN
    assert engine.close_objection(critique.id, "no", rebuttal_by="Pragmatists")
    assert critique.status == ObjectionStatus.CLOSED


def test_switch_needs_open_objection_and_citation() -> None:
    engine = make_engine()
    critique = engine.register_critique(make_critique())
    switch = make_switch(objection_id=critique.id)
    assert engine.validate_switch(switch)
    engine.close_objection(critique.id, "rebuttal", rebuttal_by="Pragmatists")
    assert not engine.validate_switch(switch)  # closed != open
    critique2 = engine.register_critique(make_critique())
    assert not engine.validate_switch(make_switch(objection_id=critique2.id, convinced_by=""))


def test_switch_only_toward_the_objections_author() -> None:
    """With 3+ factions 'any other faction' is not the convincer."""
    engine = make_engine()
    critique = engine.register_critique(make_critique())  # author: Throughputists
    assert not engine.validate_switch(make_switch(
        objection_id=critique.id, to_faction="Maximalists",  # wrong target
    ))
    assert engine.validate_switch(make_switch(objection_id=critique.id))


def test_superseded_objection_leaves_the_ledger() -> None:
    engine = make_engine()
    critique = engine.register_critique(make_critique())
    assert engine.supersede_objection(critique.id)
    assert critique.status == ObjectionStatus.SUPERSEDED
    assert not engine.validate_switch(make_switch(objection_id=critique.id))


def test_objection_cap_defers_overflow() -> None:
    """Round-9 §2: over-cap critiques are marked deferred, never dropped silently."""
    from zhoda_core.debate import Round

    engine = make_engine(max_new_per_round=2)
    round_ = Round(number=1)
    assert engine.admit(make_critique(), round_)
    assert engine.admit(make_critique(claim="a second concrete factual objection"), round_)
    assert not engine.admit(
        make_critique(claim="a third concrete factual objection"), round_,
    )
    assert len(round_.critiques) == 2
    assert round_.deferred and "objection cap" in round_.deferred[0]["reason"]


def test_source_line_becomes_rebuttal_evidence_url() -> None:
    from zhoda_core.debate import extract_source

    prose, url = extract_source(
        "The experiment needs a control.\nSOURCE: https://hbr.org/2018/05/x"
    )
    assert url == "https://hbr.org/2018/05/x"
    assert "SOURCE:" not in prose
    assert "control" in prose

    engine = make_engine()
    critique = engine.register_critique(make_critique())
    assert engine.close_objection(
        critique.id,
        "Rebuttal with a citation.\nSOURCE: https://example.com/a",
        rebuttal_by="Pragmatists",
    )
    assert critique.rebuttal_evidence_url == "https://example.com/a"
    assert "SOURCE:" not in critique.rebuttal


def test_hedge_revision_is_refused() -> None:
    from zhoda_core.guards import is_hedge_text, should_apply_revision

    pick = "Use PostgreSQL for the ledger."
    hedge = (
        "Select the data backbone based on team expertise; both Kafka and "
        "PostgreSQL entail comparable operational complexity."
    )
    assert should_apply_revision(pick, "Use PostgreSQL with partitioning.", changed=True)
    assert not should_apply_revision(pick, hedge, changed=True)
    while_hedge = (
        "While PostgreSQL offers native ACID compliance, Kafka, when augmented "
        "with streams, can support a scalable ledger. However, achieving full "
        "ACID-equivalent guarantees in Kafka is nontrivial."
    )
    assert is_hedge_text(while_hedge)
    assert not should_apply_revision(pick, while_hedge, changed=True)
    assert not should_apply_revision(pick, pick, changed=False)


def test_xor_question_rejects_hybrid_action() -> None:
    from zhoda_core.guards import is_hybrid_decision, looks_like_xor_question

    assert looks_like_xor_question("PostgreSQL or Kafka for a 50k RPS ledger?")
    assert looks_like_xor_question("Monolith vs microservices for a 4-person team?")
    assert not looks_like_xor_question("Review this login helper for production use.")
    hybrid = "Use Kafka as the event log alongside PostgreSQL — a hybrid of both."
    assert is_hybrid_decision(hybrid)
    assert not is_hybrid_decision("PostgreSQL is the preferred choice for the ledger.")


def test_generic_insecure_decision_does_not_cover_sqli_claim() -> None:
    from zhoda_core.guards import claims_reflected_in_decision, ensure_claims_in_decision

    finding = "SQL injection from interpolating user input into the query"
    generic = (
        "The provided login helper is inherently insecure and unsuitable "
        "for production use due to critical security vulnerabilities."
    )
    assert not claims_reflected_in_decision([finding], generic)
    assert "SQL injection" in ensure_claims_in_decision(generic, [finding])
    named = "Do not approve: SQL injection in the query string."
    assert claims_reflected_in_decision([finding], named)
    assert ensure_claims_in_decision(named, [finding]) == named


def test_supersede_prompt_rejects_caveat_only() -> None:
    from zhoda_core.debate import SUPERSEDE_PROMPT

    assert "NOT addressed" in SUPERSEDE_PROMPT
    assert "primary recommended action" in SUPERSEDE_PROMPT
