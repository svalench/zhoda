"""Хроніка не бывает пустой: start при create, error при падении провайдера."""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from zhoda_core.engine import ZhodaEngine
from zhoda_core.models import Protocol
from zhoda_core.providers.openrouter import ZhodaProviderError
from zhoda_core.transcripts import TranscriptStore

from .test_e2e import (
    CLASSIFIERS,
    COUNCIL,
    DECISION,
    JUDGES,
    PG,
    PLAN,
    ScriptedProvider,
    make_engine,
    position,
)


def _stages(events: list[dict[str, Any]]) -> list[str]:
    return [str(event.get("stage")) for event in events]


def _first(stages: list[str], name: str) -> int:
    try:
        return stages.index(name)
    except ValueError as exc:
        raise AssertionError(f"stage {name!r} missing in {stages}") from exc


def test_create_writes_start_event(tmp_path: Path) -> None:
    store = TranscriptStore(str(tmp_path))
    tid = store.create({"question": "PostgreSQL or Kafka?"})
    events = store.read(tid)
    assert events[0]["stage"] == "start"
    assert events[0]["question"] == "PostgreSQL or Kafka?"
    raw = (tmp_path / f"{tid}.jsonl").read_text(encoding="utf-8")
    assert raw.strip()


class _BoomProvider(ScriptedProvider):
    async def complete(
        self,
        model: str,
        prompt: str,
        *,
        cache_key: str | None = None,
        max_tokens: int = 2000,
    ) -> str:
        raise ZhodaProviderError("401: API key expired.")


@pytest.mark.asyncio
async def test_provider_crash_leaves_error_on_transcript(tmp_path: Path) -> None:
    engine = make_engine(_BoomProvider([]), tmp_path)
    with pytest.raises(ZhodaProviderError, match="401"):
        await engine.deliberate("which db?", clarify_mode="no-clarify")
    tid = engine.last_transcript_id
    assert tid
    events = engine.transcripts.read(tid)
    stages = _stages(events)
    assert stages[0] == "start"
    assert events[0]["question"] == "which db?"
    assert stages[-1] == "error"
    assert "verdict" not in stages
    assert events[-1]["error_type"] == "ZhodaProviderError"
    assert "401" in events[-1]["error"]


class _CancelProvider(ScriptedProvider):
    async def complete(self, model: str, prompt: str, **kwargs: object) -> str:
        del model, prompt, kwargs
        raise asyncio.CancelledError()


@pytest.mark.asyncio
async def test_cancellation_is_terminal_error_not_success(tmp_path: Path) -> None:
    """Cancel: accounting D (end_question) + terminal error E, не verdict."""
    engine = make_engine(_CancelProvider([]), tmp_path)
    with pytest.raises(asyncio.CancelledError):
        await engine.deliberate("which db?", clarify_mode="no-clarify")
    tid = engine.last_transcript_id
    assert tid
    events = engine.transcripts.read(tid)
    stages = _stages(events)
    assert stages[0] == "start"
    assert stages[-1] == "error"
    assert "verdict" not in stages
    assert events[-1]["error_type"] == "CancelledError"
    assert events[-1].get("terminal") is True
    assert engine.provider._active_run is None


@pytest.mark.asyncio
async def test_successful_run_orders_start_route_verdict(tmp_path: Path) -> None:
    """Контракт: start < route < verdict; промежуточные стадии допустимы."""
    script = [
        ("m1", ("independent structured stance",), position(PG)),
        ("m2", ("independent structured stance",), position(PG)),
        ("m3", ("independent structured stance",), position(PG)),
        (None, ("Synthesize the shared platform",), position(PG)),
        (None, ("Name each faction",), {}),
        (None, ("SYNTHESIZE THE COUNCIL DECISION",), DECISION),
        (None, ("PLAN CONTRACT",), PLAN),
    ]
    engine = make_engine(ScriptedProvider(script), tmp_path, devils_advocate=False)
    verdict = await engine.deliberate(
        "Does SQL NULL equal TRUE?",
        force_protocol=Protocol.VOTE,
        clarify_mode="no-clarify",
    )
    events = engine.transcripts.read(verdict.transcript_id)
    stages = _stages(events)
    start_at = _first(stages, "start")
    route_at = _first(stages, "route")
    verdict_at = _first(stages, "verdict")
    assert start_at == 0
    assert start_at < route_at < verdict_at
    assert len(stages) > 3
    assert "error" not in stages
    assert events[start_at]["question"] == "Does SQL NULL equal TRUE?"
    assert events[route_at]["protocol"] == "vote"
    assert events[verdict_at]["verdict"]["transcript_id"] == verdict.transcript_id
    assert events[verdict_at]["verdict"]["decision"] == verdict.decision


def test_engine_exposes_last_transcript_id(tmp_path: Path) -> None:
    engine = ZhodaEngine(
        ScriptedProvider([]),
        COUNCIL,
        chairman="j1",
        judges=JUDGES,
        router_classifiers=CLASSIFIERS,
        transcripts_dir=str(tmp_path),
    )
    assert engine.last_transcript_id is None


def _event(**kwargs: Any) -> dict[str, Any]:
    from zhoda_core.models import EVENT_SCHEMA_VERSION, EventType, ProtocolEvent

    base = ProtocolEvent(
        event_id=kwargs.get("event_id", "e1"),
        seq=kwargs.get("seq", 1),
        run_id=kwargs.get("run_id", "runA"),
        transcript_id="t",
        type=kwargs.get("type", EventType.RUN_STARTED),
        prev_event_id=kwargs.get("prev_event_id"),
        payload=kwargs.get("payload", {}),
    )
    dump = base.model_dump()
    dump["schema_version"] = kwargs.get("schema_version", EVENT_SCHEMA_VERSION)
    if "type" in kwargs and not hasattr(kwargs["type"], "value"):
        dump["type"] = kwargs["type"]
    return {"stage": "protocol_event", "event": dump}


def test_replay_rejects_unknown_schema_version() -> None:
    from zhoda_core.replay import ReplayError, reduce_transcript

    row = _event()
    row["event"]["schema_version"] = "zhoda.events.v0"
    with pytest.raises(ReplayError, match="unknown schema version"):
        reduce_transcript([row])


def test_replay_rejects_cross_run_id() -> None:
    from zhoda_core.models import EventType
    from zhoda_core.replay import ReplayError, reduce_transcript

    first = _event(event_id="a", seq=1, run_id="runA", type=EventType.RUN_STARTED)
    second = _event(
        event_id="b", seq=2, run_id="runB", type=EventType.RENDERING,
        prev_event_id="a", payload={"decision": "x"},
    )
    with pytest.raises(ReplayError, match="cross-run"):
        reduce_transcript([first, second])


def test_replay_rejects_seq_gap() -> None:
    from zhoda_core.models import EventType
    from zhoda_core.replay import ReplayError, reduce_transcript

    first = _event(event_id="a", seq=1, type=EventType.RUN_STARTED)
    third = _event(
        event_id="c", seq=3, type=EventType.RENDERING,
        prev_event_id="a", payload={"decision": "x"},
    )
    with pytest.raises(ReplayError, match="seq gap"):
        reduce_transcript([first, third])


def test_replay_duplicate_event_is_idempotent() -> None:
    from zhoda_core.models import EventType
    from zhoda_core.replay import reduce_transcript

    first = _event(event_id="a", seq=1, type=EventType.RUN_STARTED)
    render = _event(
        event_id="b", seq=2, type=EventType.RENDERING,
        prev_event_id="a", payload={"decision": "Reject login"},
    )
    dup = dict(render)
    dup["event"] = dict(render["event"])
    state = reduce_transcript([first, render, dup])
    assert state.decision == "Reject login"
    assert state.last_seq == 2


def test_replay_duplicate_conflict_is_error() -> None:
    from zhoda_core.models import EventType
    from zhoda_core.replay import ReplayError, reduce_transcript

    first = _event(event_id="a", seq=1, type=EventType.RUN_STARTED)
    render = _event(
        event_id="b", seq=2, type=EventType.RENDERING,
        prev_event_id="a", payload={"decision": "A"},
    )
    dup = dict(render)
    dup["event"] = dict(render["event"])
    dup["event"]["payload"] = {"decision": "B"}
    with pytest.raises(ReplayError, match="conflicting payload"):
        reduce_transcript([first, render, dup])


def test_replay_tampered_claim_transition_is_rejected() -> None:
    from zhoda_core.models import ClaimState, EventType
    from zhoda_core.replay import ReplayError, reduce_transcript

    start = _event(event_id="a", seq=1, type=EventType.RUN_STARTED)
    created = _event(
        event_id="b", seq=2, type=EventType.CLAIM_CREATED, prev_event_id="a",
        payload={
            "claim": "There is no SQL injection.",
            "claim_id": "clm_x",
            "state": ClaimState.ACTIVE.value,
        },
    )
    bad = _event(
        event_id="c", seq=3, type=EventType.CLAIM_TRANSITION, prev_event_id="b",
        payload={"claim_id": "clm_x", "to_state": "vanished", "version": 2},
    )
    with pytest.raises(ReplayError, match="invalid claim to_state"):
        reduce_transcript([start, created, bad])


def test_replay_corrupted_event_is_not_partial_success() -> None:
    from zhoda_core.replay import ReplayError, reduce_transcript

    start = _event(event_id="a", seq=1)
    with pytest.raises(ReplayError, match="invalid protocol_event"):
        reduce_transcript([start, {"stage": "protocol_event", "event": {"nope": True}}])


def test_replay_restores_switch_from_events_not_verdict_row() -> None:
    from zhoda_core.models import EventType
    from zhoda_core.replay import reduce_transcript

    start = _event(event_id="a", seq=1, type=EventType.RUN_STARTED)
    switch = _event(
        event_id="b", seq=2, type=EventType.SWITCH_RECORDED, prev_event_id="a",
        payload={
            "model": "Response A",
            "from_faction": "Pragmatists",
            "to_faction": "Throughputists",
            "convinced_by": "PostgreSQL handles 50k RPS writes on a single node",
            "objection_id": "obj1",
        },
    )
    consensus = _event(
        event_id="c", seq=3, type=EventType.CONSENSUS_SET, prev_event_id="b",
        payload={"consensus_strength": "split", "zhoda_reached": False},
    )
    state = reduce_transcript([start, switch, consensus])
    assert len(state.switches) == 1
    assert state.switches[0].objection_id == "obj1"
    assert state.zhoda_reached is False


def test_replay_verdict_row_alone_is_not_success() -> None:
    from zhoda_core.replay import ReplayError, reduce_transcript

    with pytest.raises(ReplayError, match="empty event log"):
        reduce_transcript(
            [{"stage": "verdict", "verdict": {"decision": "Reject login", "zhoda_reached": True}}]
        )


def test_replay_rejects_illegal_claim_revival() -> None:
    from zhoda_core.models import ClaimState, EventType
    from zhoda_core.replay import ReplayError, reduce_transcript

    start = _event(event_id="a", seq=1, type=EventType.RUN_STARTED)
    created = _event(
        event_id="b", seq=2, type=EventType.CLAIM_CREATED, prev_event_id="a",
        payload={
            "claim": "There is no SQL injection.",
            "claim_id": "clm_x",
            "state": ClaimState.ACTIVE.value,
        },
    )
    refuted = _event(
        event_id="c", seq=3, type=EventType.CLAIM_TRANSITION, prev_event_id="b",
        payload={"claim_id": "clm_x", "to_state": ClaimState.REFUTED.value, "version": 2},
    )
    revive = _event(
        event_id="d", seq=4, type=EventType.CLAIM_TRANSITION, prev_event_id="c",
        payload={"claim_id": "clm_x", "to_state": ClaimState.ACTIVE.value, "version": 3},
    )
    with pytest.raises(ReplayError, match="illegal claim transition"):
        reduce_transcript([start, created, refuted, revive])


def test_evidence_bundle_redacts_secrets_and_limits_replay() -> None:
    from zhoda_core.evidence import bundle_from_context
    from zhoda_core.models import AccessStatus

    bundle = bundle_from_context(
        "token OPENROUTER_API_KEY=sk-testsecret99 password=hunter2"
    )
    assert bundle is not None
    assert bundle.access is AccessStatus.REDACTED
    assert bundle.replay_limited is True
    assert bundle.content is None
    assert bundle.spans == []
    dumped = str(bundle.manifest())
    assert "sk-testsecret99" not in dumped
    assert "hunter2" not in dumped


def test_minority_claims_stay_on_owner_not_majority() -> None:
    from zhoda_core.claims import collect_ledger, supporting_claims
    from zhoda_core.models import Claim, ClaimState, Position

    majority = Position(
        model="A",
        thesis="Reject login",
        answer="no",
        claims=[
            Claim(
                claim="SQL injection from interpolating user input",
                claim_id="clm_maj",
                owner="A",
                state=ClaimState.ACTIVE,
            )
        ],
    )
    minority = Position(
        model="B",
        thesis="Approve login",
        answer="ok",
        claims=[
            Claim(
                claim="There is no SQL injection.",
                claim_id="clm_min",
                owner="B",
                state=ClaimState.ACTIVE,
            )
        ],
    )
    ledger = collect_ledger([majority, minority])
    assert {c.claim_id for c in ledger} == {"clm_maj", "clm_min"}
    assert all(c.owner == "B" for c in ledger if c.claim_id == "clm_min")
    assert {c.claim_id for c in supporting_claims(majority.claims)} == {"clm_maj"}
    assert "clm_min" not in {c.claim_id for c in supporting_claims(majority.claims)}
