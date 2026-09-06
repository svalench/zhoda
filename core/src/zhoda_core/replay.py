"""Pure event reducer: structural state from initial snapshot + transitions.

Final Verdict JSONL row is NOT trusted input. Unknown schema / gap / cross-run
→ ReplayError, не частичный success.
Idempotency: повтор event_id с тем же payload — no-op; иначе ошибка.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from .models import (
    EVENT_SCHEMA_VERSION,
    ActionContract,
    Claim,
    ClaimState,
    ConsensusStrength,
    Disagreement,
    EventType,
    FactionSwitch,
    ProtocolEvent,
    ReplayState,
    RunCompleteness,
)


class ReplayError(ValueError):
    """Явная ошибка replay: лог нельзя принять как успешный state."""


_ALLOWED_CLAIM = {
    (ClaimState.ACTIVE, ClaimState.REFUTED),
    (ClaimState.ACTIVE, ClaimState.SUPERSEDED),
}


class EventLog:
    """Живой лог одного run. seq монотонный; sink пишет в хроніку."""

    def __init__(
        self,
        *,
        run_id: str,
        transcript_id: str,
        sink: Callable[[ProtocolEvent], None] | None = None,
    ) -> None:
        self.run_id = run_id
        self.transcript_id = transcript_id
        self.sink = sink
        self.events: list[ProtocolEvent] = []
        self._prev: str | None = None

    def emit(
        self,
        event_type: EventType,
        payload: dict[str, object],
        *,
        attribution: str = "",
    ) -> ProtocolEvent:
        event = ProtocolEvent(
            event_id=uuid4().hex[:16],
            seq=len(self.events) + 1,
            run_id=self.run_id,
            transcript_id=self.transcript_id,
            type=event_type,
            prev_event_id=self._prev,
            attribution=attribution,
            payload=payload,
        )
        self.events.append(event)
        self._prev = event.event_id
        if self.sink is not None:
            self.sink(event)
        return event


def events_from_transcript(rows: list[dict[str, Any]]) -> list[ProtocolEvent]:
    """Только protocol_event. stage=verdict не является reducer input."""
    found: list[ProtocolEvent] = []
    for row in rows:
        if row.get("stage") != "protocol_event":
            continue
        raw = row.get("event", row)
        if not isinstance(raw, dict):
            raise ReplayError("protocol_event payload is not an object")
        try:
            found.append(ProtocolEvent.model_validate(raw))
        except ValidationError as exc:
            raise ReplayError(f"invalid protocol_event schema: {exc}") from exc
    return found


def reduce_events(events: list[ProtocolEvent]) -> ReplayState:
    """Чистый reducer. Без provider, без cache, без HTTP."""
    if not events:
        raise ReplayError("empty event log")
    state = ReplayState(run_id=events[0].run_id)
    seen_ids: dict[str, ProtocolEvent] = {}
    last_seq = 0
    last_id: str | None = None
    claims: dict[str, Claim] = {}

    for event in events:
        if event.schema_version != EVENT_SCHEMA_VERSION:
            raise ReplayError(
                f"unknown schema version {event.schema_version!r}"
            )
        if event.run_id != state.run_id:
            raise ReplayError(
                f"cross-run id {event.run_id!r} (expected {state.run_id!r})"
            )
        prior = seen_ids.get(event.event_id)
        if prior is not None:
            if prior.payload != event.payload or prior.type != event.type:
                raise ReplayError(
                    f"duplicate event_id {event.event_id} with conflicting payload"
                )
            continue  # idempotent no-op
        if event.seq != last_seq + 1:
            raise ReplayError(
                f"event seq gap: expected {last_seq + 1}, got {event.seq}"
            )
        if event.prev_event_id != last_id:
            raise ReplayError(
                f"invalid prev_event_id on seq {event.seq}"
            )
        _apply(state, claims, event)
        seen_ids[event.event_id] = event
        last_seq = event.seq
        last_id = event.event_id

    state.claims = list(claims.values())
    state.last_seq = last_seq
    return state


def reduce_transcript(rows: list[dict[str, Any]]) -> ReplayState:
    return reduce_events(events_from_transcript(rows))


def _apply(state: ReplayState, claims: dict[str, Claim], event: ProtocolEvent) -> None:
    payload = event.payload
    if event.type is EventType.RUN_STARTED:
        return
    if event.type is EventType.EVIDENCE_ATTACHED:
        state.replay_limited = bool(payload.get("replay_limited"))
        return
    if event.type is EventType.CLAIM_CREATED:
        claim = Claim.model_validate(payload)
        if not claim.claim_id:
            raise ReplayError("CLAIM_CREATED missing claim_id")
        if claim.claim_id in claims:
            raise ReplayError(f"CLAIM_CREATED duplicate {claim.claim_id}")
        claims[claim.claim_id] = claim
        return
    if event.type is EventType.CLAIM_TRANSITION:
        cid = str(payload.get("claim_id") or "")
        if cid not in claims:
            raise ReplayError(f"CLAIM_TRANSITION unknown claim {cid}")
        try:
            to_state = ClaimState(str(payload.get("to_state")))
        except ValueError as exc:
            raise ReplayError(f"invalid claim to_state: {payload.get('to_state')}") from exc
        current = claims[cid]
        if (current.state, to_state) not in _ALLOWED_CLAIM and current.state != to_state:
            raise ReplayError(
                f"illegal claim transition {current.state} → {to_state}"
            )
        raw_version = payload.get("version")
        if isinstance(raw_version, int) and not isinstance(raw_version, bool):
            version = raw_version
        elif raw_version is None:
            version = current.version + 1
        else:
            raise ReplayError(f"invalid claim version: {raw_version}")
        claims[cid] = current.model_copy(
            update={
                "state": to_state,
                "version": version,
                "replaced_by": payload.get("replaced_by") or current.replaced_by,
            }
        )
        return
    if event.type is EventType.ACTION_SET:
        state.action = ActionContract.model_validate(payload)
        return
    if event.type is EventType.SWITCH_RECORDED:
        state.switches.append(FactionSwitch.model_validate(payload))
        return
    if event.type is EventType.DISSENT_SET:
        raw = payload.get("dissent") or []
        if not isinstance(raw, list):
            raise ReplayError("DISSENT_SET.dissent must be a list")
        state.dissent = [Disagreement.model_validate(item) for item in raw]
        return
    if event.type is EventType.CONSENSUS_SET:
        strength = payload.get("consensus_strength")
        state.consensus_strength = (
            ConsensusStrength(str(strength)) if strength else None
        )
        zhoda = payload.get("zhoda_reached")
        if zhoda is not None and not isinstance(zhoda, bool):
            raise ReplayError("CONSENSUS_SET.zhoda_reached must be bool")
        state.zhoda_reached = bool(zhoda) if zhoda is not None else None
        return
    if event.type is EventType.COMPLETENESS_SET:
        state.completeness = RunCompleteness.model_validate(payload)
        return
    if event.type is EventType.RENDERING:
        state.decision = str(payload.get("decision") or "")
        return
    if event.type in (
        EventType.OBJECTION_REGISTERED,
        EventType.OBJECTION_TRANSITION,
    ):
        return
    raise ReplayError(f"unsupported event type {event.type}")
