"""Unit tests for the objection ledger gates — no provider calls."""

import pytest

from zhoda_core.debate import (
    CLOSURE_PROMPT,
    SWITCH_PROMPT,
    DebateEngine,
    citation_quotes_objection,
    is_concede_rebuttal,
)
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
        "convinced_by": (
            "PostgreSQL handles 50k RPS writes on a single node is false"
        ),
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


def test_xor_pick_flip_revision_is_refused() -> None:
    from zhoda_core.guards import should_apply_revision

    question = "Monolith vs microservices for a 4-person team building a B2B SaaS MVP?"
    old = "For a 4-person team, a monolithic architecture is generally preferable."
    flipped = (
        "Adopting a microservices architecture from the start can be beneficial "
        "despite the small team size."
    )
    assert not should_apply_revision(old, flipped, changed=True, question=question)
    caveated = old + " Keep modules ready to extract later."
    assert should_apply_revision(old, caveated, changed=True, question=question)


def test_xor_always_option_is_not_loaded_premise() -> None:
    from zhoda_core.guards import looks_like_loaded_premise

    assert not looks_like_loaded_premise(
        "Pin lockfiles or always install the latest patch of every dependency?"
    )
    assert looks_like_loaded_premise(
        "Why is REST always faster than gRPC for internal microservice calls?"
    )
    assert looks_like_loaded_premise(
        "Since containers always add overhead, should we run the API as root?"
    )


def test_loaded_premise_revision_is_refused() -> None:
    from zhoda_core.guards import should_apply_revision

    question = "Why is REST always faster than gRPC for internal microservice calls?"
    challenged = "The premise is false: gRPC is typically faster than REST."
    adopted = "REST is recommended over gRPC because it often yields faster responses."
    assert not should_apply_revision(challenged, adopted, changed=True, question=question)
    assert should_apply_revision(
        challenged, challenged + " Prefer protobuf on the mesh.", changed=True,
        question=question,
    )


def test_b1_given_that_does_not_fabricate_false_premise() -> None:
    from zhoda_core.guards import (
        LOADED_PREMISE_REJECT,
        ensure_loaded_premise_not_adopted,
        should_apply_revision,
    )

    question = "Given that our team has four engineers, should we use a monolith?"
    candidate = "Use a monolith to minimize coordination overhead."
    assert ensure_loaded_premise_not_adopted(question, candidate) == candidate
    assert LOADED_PREMISE_REJECT not in ensure_loaded_premise_not_adopted(
        question, candidate
    )
    caveated = candidate + " Keep module boundaries clean."
    assert should_apply_revision(candidate, caveated, changed=True, question=question)


def test_xor_paraphrase_is_not_a_pick_flip() -> None:
    from zhoda_core.guards import should_apply_revision

    question = "PostgreSQL or Kafka for a 50k RPS ledger?"
    old = "Use PostgreSQL, not Kafka."
    paraphrase = "Instead of Kafka, use PostgreSQL."
    reject_kafka = "Reject Kafka. PostgreSQL remains the recommendation."
    flipped = "PostgreSQL is unsuitable; use Kafka."
    assert should_apply_revision(old, paraphrase, changed=True, question=question)
    assert should_apply_revision(old, reject_kafka, changed=True, question=question)
    assert not should_apply_revision(old, flipped, changed=True, question=question)
    assert should_apply_revision(
        old,
        flipped,
        changed=True,
        question=question,
        correction=True,
        change_note="open objection showed PostgreSQL cannot hold 50k RPS writes",
    )


def test_loaded_premise_switch_is_blocked() -> None:
    from zhoda_core.guards import blocks_loaded_premise_switch

    question = "Why is REST always faster than gRPC for internal microservice calls?"
    challenged = "The premise is false: gRPC is typically faster than REST."
    adopted = "REST is recommended over gRPC because it often yields faster responses."
    assert blocks_loaded_premise_switch(question, challenged, adopted)
    assert not blocks_loaded_premise_switch(question, adopted, challenged)
    assert not blocks_loaded_premise_switch(
        "PostgreSQL or Kafka for a ledger?", challenged, adopted,
    )


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


def test_closure_prompt_rejects_acknowledgment() -> None:
    assert "CONCEDE" in CLOSURE_PROMPT
    assert "closed=false" in CLOSURE_PROMPT


def test_switch_prompt_requires_quote_not_destination_thesis() -> None:
    assert "Do you switch factions?" in SWITCH_PROMPT
    assert "not restate the opposing thesis" in SWITCH_PROMPT
    assert "loaded premise" in SWITCH_PROMPT


def test_concede_rebuttal_is_detected() -> None:
    assert is_concede_rebuttal("CONCEDE")
    assert is_concede_rebuttal("concede — no counter-argument")
    assert not is_concede_rebuttal("We accept the point.")
    assert not is_concede_rebuttal("Partitioning is solved.")


def test_switch_citation_must_quote_the_objection() -> None:
    claim = "PostgreSQL handles 50k RPS writes on a single node"
    assert citation_quotes_objection(
        "PostgreSQL handles 50k RPS writes on a single node is false",
        claim,
    )
    assert not citation_quotes_objection("", claim)
    assert not citation_quotes_objection("Kafka is the simpler operational story", claim)
    engine = make_engine()
    critique = engine.register_critique(make_critique())
    assert engine.validate_switch(make_switch(objection_id=critique.id))
    assert not engine.validate_switch(make_switch(
        objection_id=critique.id,
        convinced_by="the destination thesis restated",
    ))


@pytest.mark.asyncio
async def test_concede_skips_closure_and_debate_calls_are_cached() -> None:
    """CONCEDE не зовёт судей закрытия; все LLM-вызовы несут cache_key."""
    from zhoda_core.factions import Faction
    from zhoda_core.judges import Judges
    from zhoda_core.models import Position

    keys: list[str | None] = []
    closure_asked = {"n": 0}

    class Spy:
        async def complete(self, model, prompt, *, cache_key=None, **kwargs):
            del model, kwargs
            keys.append(cache_key)
            if "Rebut it concisely" in prompt:
                return "CONCEDE"
            raise AssertionError(prompt[:120])

        async def ask_json(self, model, prompt, *, cache_key=None, **kwargs):
            del model, kwargs
            keys.append(cache_key)
            if "Did the rebuttal" in prompt:
                closure_asked["n"] += 1
                return {"closed": True}
            if "Produce ONE" in prompt:
                if 'You represent faction "Pragmatists"' in prompt:
                    target = "Throughputists"
                else:
                    target = "Pragmatists"
                return {
                    "target_faction": target,
                    "flaw_type": "factual",
                    "claim": "PostgreSQL handles 50k RPS writes on a single node",
                    "specifics": "",
                    "evidence_url": None,
                }
            if "Revise your platform" in prompt:
                return {"changed": False, "thesis": "Use PostgreSQL (simple, sufficient)"}
            if "Do you switch factions?" in prompt:
                return {"switch": False, "convinced_by": ""}
            return {}

    pg = Position(model="Response A", thesis="Use PostgreSQL (simple, sufficient)", answer="pg")
    kf = Position(model="Response B", thesis="Use Kafka (built for throughput)", answer="kf")
    factions = [
        Faction(name="Pragmatists", members=["Response A"], platform=pg),
        Faction(name="Throughputists", members=["Response B"], platform=kf),
    ]
    engine = DebateEngine(provider=Spy())  # type: ignore[arg-type]
    round_ = await engine.run_round(
        1,
        factions,
        speakers={"Response A": "m1", "Response B": "m2"},
        judges=Judges(("j1", "j2"), {}),
    )
    assert closure_asked["n"] == 0
    assert all(c.status is ObjectionStatus.OPEN for c in round_.critiques)
@pytest.mark.asyncio
async def test_c2_string_false_does_not_close_objection() -> None:
    factions, round_ = await _two_faction_round(closure={"closed": "false"})
    assert all(c.status is ObjectionStatus.OPEN for c in round_.critiques)
    assert round_.parse_failures
    assert any(f.stage == "closure" for f in round_.parse_failures)
    assert factions[0].members == ["Response A"]


@pytest.mark.asyncio
async def test_c2_bool_false_stays_open_bool_true_closes() -> None:
    _, open_round = await _two_faction_round(closure={"closed": False})
    assert all(c.status is ObjectionStatus.OPEN for c in open_round.critiques)
    _, closed_round = await _two_faction_round(closure={"closed": True})
    assert closed_round.critiques
    assert all(c.status is ObjectionStatus.CLOSED for c in closed_round.critiques)


@pytest.mark.asyncio
async def test_c3_string_false_does_not_apply_revision() -> None:
    new_thesis = "Use PostgreSQL with a partitioning plan for write scaling"
    factions, round_ = await _two_faction_round(
        closure={"closed": False},
        revise_pg={
            "thesis": new_thesis,
            "answer": new_thesis,
            "changed": "false",
            "change_note": "added partitioning",
            "confidence": 0.8,
        },
    )
    assert factions[0].platform is not None
    assert factions[0].platform.thesis == "Use PostgreSQL (simple, sufficient)"
    assert round_.revisions == []


@pytest.mark.asyncio
async def test_c3_bool_true_applies_revision() -> None:
    new_thesis = "Use PostgreSQL with a partitioning plan for write scaling"
    factions, round_ = await _two_faction_round(
        closure={"closed": False},
        revise_pg={
            "thesis": new_thesis,
            "answer": new_thesis,
            "changed": True,
            "change_note": "added partitioning",
            "confidence": 0.8,
        },
        addressed={"addressed": False},
    )
    assert factions[0].platform is not None
    assert factions[0].platform.thesis == new_thesis
    assert round_.revisions


@pytest.mark.asyncio
async def test_c3_string_true_does_not_apply_revision() -> None:
    """StrictBool: строка \"true\" — не переход."""
    new_thesis = "Use PostgreSQL with a partitioning plan for write scaling"
    factions, round_ = await _two_faction_round(
        closure={"closed": False},
        revise_pg={
            "thesis": new_thesis,
            "answer": new_thesis,
            "changed": "true",
            "change_note": "added partitioning",
            "confidence": 0.8,
        },
    )
    assert factions[0].platform is not None
    assert factions[0].platform.thesis == "Use PostgreSQL (simple, sufficient)"
    assert round_.revisions == []


@pytest.mark.asyncio
async def test_c4_string_false_does_not_supersede() -> None:
    new_thesis = "Use PostgreSQL with a partitioning plan for write scaling"
    factions, round_ = await _two_faction_round(
        closure={"closed": False},
        revise_pg={
            "thesis": new_thesis,
            "answer": new_thesis,
            "changed": True,
            "change_note": "added partitioning",
            "confidence": 0.8,
        },
        withdraw={"withdraw": False},
        addressed={"addressed": "false"},
    )
    assert factions[0].platform is not None
    assert factions[0].platform.thesis == new_thesis
    against = [c for c in round_.critiques if c.target_faction == "Pragmatists"]
    assert against
    assert all(c.status is ObjectionStatus.OPEN for c in against)


@pytest.mark.asyncio
async def test_c4_bool_true_supersedes_after_revision() -> None:
    new_thesis = "Use PostgreSQL with a partitioning plan for write scaling"
    _, round_ = await _two_faction_round(
        closure={"closed": False},
        revise_pg={
            "thesis": new_thesis,
            "answer": new_thesis,
            "changed": True,
            "change_note": "added partitioning",
            "confidence": 0.8,
        },
        withdraw={"withdraw": False},
        addressed={"addressed": True},
    )
    against = [c for c in round_.critiques if c.target_faction == "Pragmatists"]
    assert against
    assert all(c.status is ObjectionStatus.SUPERSEDED for c in against)


@pytest.mark.asyncio
async def test_c5_string_false_does_not_move_member() -> None:
    factions, round_ = await _two_faction_round(
        closure={"closed": False},
        switch={
            "switch": "false",
            "convinced_by": "PostgreSQL handles 50k RPS writes on a single node",
        },
    )
    assert factions[0].members == ["Response A"]
    assert factions[1].members == ["Response B"]
    assert round_.switches == []


@pytest.mark.asyncio
async def test_c5_bool_true_moves_member_bool_false_does_not() -> None:
    claim = "PostgreSQL handles 50k RPS writes on a single node"
    factions_stay, _ = await _two_faction_round(
        closure={"closed": False},
        switch={"switch": False, "convinced_by": claim},
    )
    assert factions_stay[0].members == ["Response A"]
    factions_move, round_ = await _two_faction_round(
        closure={"closed": False},
        switch={"switch": True, "convinced_by": claim},
    )
    assert "Response A" in factions_move[1].members
    assert "Response A" not in factions_move[0].members
    assert round_.switches


async def _two_faction_round(
    *,
    closure: dict | None = None,
    revise_pg: dict | None = None,
    revise_kf: dict | None = None,
    withdraw: dict | None = None,
    addressed: dict | None = None,
    switch: dict | None = None,
):
    from zhoda_core.factions import Faction
    from zhoda_core.judges import Judges
    from zhoda_core.models import Position

    closure = closure or {"closed": False}
    revise_pg = revise_pg or {
        "changed": False,
        "thesis": "Use PostgreSQL (simple, sufficient)",
        "answer": "pg",
        "change_note": "",
    }
    revise_kf = revise_kf or {
        "changed": False,
        "thesis": "Use Kafka (built for throughput)",
        "answer": "kf",
        "change_note": "",
    }
    withdraw = withdraw or {"withdraw": False}
    addressed = addressed or {"addressed": False}
    switch = switch or {
        "switch": False,
        "convinced_by": "the objection overstates operational cost",
    }

    class Spy:
        async def complete(self, model, prompt, *, cache_key=None, **kwargs):
            del model, cache_key, kwargs
            if "Rebut it concisely" in prompt:
                return "Partitioning is a solved operational pattern."
            raise AssertionError(prompt[:120])

        async def ask_json(self, model, prompt, *, cache_key=None, **kwargs):
            del model, cache_key, kwargs
            if "Did the rebuttal" in prompt:
                return closure
            if "Produce ONE" in prompt:
                if 'You represent faction "Pragmatists"' in prompt:
                    return {
                        "target_faction": "Throughputists",
                        "flaw_type": "factual",
                        "claim": "Kafka adds operational complexity the team cannot staff",
                        "specifics": "",
                        "evidence_url": None,
                    }
                return {
                    "target_faction": "Pragmatists",
                    "flaw_type": "factual",
                    "claim": "PostgreSQL handles 50k RPS writes on a single node",
                    "specifics": "",
                    "evidence_url": None,
                }
            if "Revise your platform" in prompt:
                if 'Your faction "Pragmatists"' in prompt:
                    return revise_pg
                return revise_kf
            if "withdraw your objection" in prompt:
                return withdraw
            if "Does the revised platform" in prompt:
                return addressed
            if "Do you switch factions?" in prompt:
                return switch
            return {}

    pg = Position(
        model="Response A",
        thesis="Use PostgreSQL (simple, sufficient)",
        answer="pg",
    )
    kf = Position(
        model="Response B",
        thesis="Use Kafka (built for throughput)",
        answer="kf",
    )
    factions = [
        Faction(name="Pragmatists", members=["Response A"], platform=pg),
        Faction(name="Throughputists", members=["Response B"], platform=kf),
    ]
    engine = DebateEngine(provider=Spy())  # type: ignore[arg-type]
    round_ = await engine.run_round(
        1,
        factions,
        speakers={"Response A": "m1", "Response B": "m2"},
        judges=Judges(("j1", "j2"), {}),
    )
    return factions, round_

