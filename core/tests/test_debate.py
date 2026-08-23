"""Unit tests for the objection ledger gates — no provider calls."""

import pytest

from zhoda_core.debate import DebateEngine
from zhoda_core.models import Critique, FactionSwitch, FlawType, ObjectionStatus


def make_engine() -> DebateEngine:
    return DebateEngine(provider=None)  # gates don't touch the provider


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
    """Round-7 §3: with 3+ factions 'any other faction' is not the convincer."""
    engine = make_engine()
    critique = engine.register_critique(make_critique())  # author: Throughputists
    assert not engine.validate_switch(make_switch(
        objection_id=critique.id, to_faction="Maximalists",  # wrong target
    ))
    assert engine.validate_switch(make_switch(objection_id=critique.id))


def test_superseded_objection_leaves_the_ledger() -> None:
    engine = make_engine()
    critique = engine.register_critique(makecritique())
    assert engine.supersede_objection(critique.id)
    assert critique.status == ObjectionStatus.SUPERSEDED
    assert not engine.validate_switch(make_switch(objection_id=critique.id))
