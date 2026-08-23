"""Unit tests for the objection ledger gates — no provider calls."""

import pytest

from zhoda_core.debate import DebateEngine
from zhoda_core.models import Critique, FactionSwitch, FlawType, ObjectionStatus


def make_engine() -> DebateEngine:
    return DebateEngine(provider=None)  # gates don't touch the provider


def make_critique(**kwargs) -> Critique:
    base = {
        "target_faction": "Pragmatists",
        "flaw_type": FlawType.FACTUAL,
        "claim": "PostgreSQL handles 50k RPS writes on a single node",
    }
    return Critique(**(base | kwargs))


def test_vague_scope_critique_is_rejected() -> None:
    engine = make_engine()
    with pytest.raises(ValueError):
        engine.register_critique(make_critique(
            flaw_type=FlawType.SCOPE,
            claim="you did not consider important aspects of scalability and more",
            specifics="",  # no specifics -> rejected
        ))


def test_close_requires_rebuttal_from_target_faction() -> None:
    engine = make_engine()
    critique = engine.register_critique(make_critique())
    assert not engine.close_objection(critique.id, "no it does not", rebuttal_by="Opponents")
    assert critique.status == ObjectionStatus.OPEN
    assert engine.close_objection(critique.id, "no it does not", rebuttal_by="Pragmatists")
    assert critique.status == ObjectionStatus.CLOSED


def test_switch_needs_both_halves() -> None:
    engine = make_engine()
    critique = engine.register_critique(make_critique())
    switch = FactionSwitch(
        model="Response A", from_faction="Pragmatists", to_faction="Throughputists",
        convinced_by="the write-scaling argument", objection_id=critique.id,
    )
    assert engine.validate_switch(switch)
    # half one missing: objection closed
    engine.close_objection(critique.id, "rebuttal", rebuttal_by="Pragmatists")
    assert not engine.validate_switch(switch)
    # half two missing: empty citation
    critique2 = engine.register_critique(make_critique())
    assert not engine.validate_switch(FactionSwitch(
        model="Response A", from_faction="Pragmatists", to_faction="Throughputists",
        convinced_by="", objection_id=critique2.id,
    ))


def test_superseded_objection_leaves_the_ledger() -> None:
    engine = make_engine()
    critique = engine.register_critique(make_critique())
    assert engine.supersede_objection(critique.id)
    assert critique.status == ObjectionStatus.SUPERSEDED
    switch = FactionSwitch(
        model="Response A", from_faction="Pragmatists", to_faction="Throughputists",
        convinced_by="the write-scaling argument", objection_id=critique.id,
    )
    assert not engine.validate_switch(switch)  # superseded != open
