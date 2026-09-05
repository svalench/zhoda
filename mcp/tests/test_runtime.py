"""Инструменты MCP: confirm не тратит; quota — честная ошибка; JSON, не проза."""

from __future__ import annotations

from typing import Any

import pytest
from zhoda_core.models import ConsensusStrength, Protocol, Verdict
from zhoda_core.providers.openrouter import QuotaExceededError
from zhoda_core.reputation import Domain, DomainEloMatrix, ReputationStorage
from zhoda_core.transcripts import TranscriptStore

from zhoda_mcp.render import last_verdict
from zhoda_mcp.runtime import Runtime

CFG = {
    "council": ["m1", "m2", "m3"],
    "judges": ["j1", "j2"],
    "router_classifiers": ["j1", "j2"],
    "chairman": "m1",
    "rounds_cap": 4,
    "budget_per_question_usd": 5.0,
    "prices": {"m1": 0.001, "m2": 0.001, "m3": 0.001},
}


def _verdict(tid: str = "abc123abc123") -> Verdict:
    return Verdict(
        decision="use postgres",
        zhoda_reached=True,
        consensus_strength=ConsensusStrength.UNANIMOUS,
        protocol=Protocol.VOTE,
        transcript_id=tid,
    )


class FakeProvider:
    async def close(self) -> None:
        return None


class FakeEngine:
    def __init__(self, error: Exception | None = None, verdict: Verdict | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._error = error
        self._verdict = verdict or _verdict()

    async def deliberate(self, question: str, **kwargs: Any) -> Verdict:
        self.calls.append((question, kwargs))
        if self._error is not None:
            raise self._error
        return self._verdict


def _runtime(
    tmp_path,
    engine: FakeEngine | None = None,
    *,
    remote: str | None = None,
    elicit_fn=None,
) -> tuple[Runtime, FakeEngine]:
    fake = engine or FakeEngine()
    provider = FakeProvider()

    def factory(_rounds: int | None) -> tuple[FakeEngine, FakeProvider]:
        return fake, provider

    rt = Runtime(
        CFG,
        transcripts=TranscriptStore(str(tmp_path / "tr")),
        reputation=ReputationStorage(tmp_path / "rep.json"),
        remote_core_url=remote,
        session_factory=factory,
        elicit_fn=elicit_fn,
    )
    return rt, fake


@pytest.mark.asyncio
async def test_confirm_false_never_runs_engine(tmp_path) -> None:
    rt, engine = _runtime(tmp_path)
    out = await rt.deliberate("which db?", confirm=False)
    assert out["status"] == "estimate"
    assert out["estimate"]["confirm_required"] is True
    assert engine.calls == []


@pytest.mark.asyncio
async def test_confirm_true_returns_verdict_json(tmp_path) -> None:
    rt, engine = _runtime(tmp_path)
    out = await rt.deliberate("which db?", confirm=True, protocol="vote")
    assert out["status"] == "verdict"
    assert out["verdict"]["decision"] == "use postgres"
    assert engine.calls[0][0] == "which db?"
    assert engine.calls[0][1]["force_protocol"] == Protocol.VOTE


@pytest.mark.asyncio
async def test_quota_exceeded_is_structured(tmp_path) -> None:
    rt, _ = _runtime(tmp_path, FakeEngine(error=QuotaExceededError("daily cap")))
    out = await rt.deliberate("which db?", confirm=True)
    assert out["error"] == "quota_exceeded"
    assert "silently" in out["hint"]
    assert "daily cap" in out["message"]


@pytest.mark.asyncio
async def test_supplied_value_map_skips_elicit_mode(tmp_path) -> None:
    rt, engine = _runtime(tmp_path)
    payload = {"constraints": ["team of four"], "goal": "pick a store"}
    await rt.deliberate("which db?", confirm=True, value_map=payload)
    kwargs = engine.calls[0][1]
    assert kwargs["value_map"] is not None
    assert kwargs["value_map"].constraints == ["team of four"]
    assert kwargs["clarify_mode"] == "no-clarify"


@pytest.mark.asyncio
async def test_invalid_protocol_does_not_open_session(tmp_path) -> None:
    rt, engine = _runtime(tmp_path)
    out = await rt.deliberate("q", confirm=True, protocol="duel")
    assert out["error"] == "invalid_protocol"
    assert engine.calls == []


@pytest.mark.asyncio
async def test_clarify_returns_questions_and_estimate(tmp_path) -> None:
    async def elicit(question: str, context: str) -> dict:
        return {
            "questions": [{"question": "Budget?", "why_it_matters": "ops", "options": ["0", "flex"]}],
            "all_questions": [],
            "ambiguity_score": 1.0,
            "open_ambiguities": [],
        }

    rt, _ = _runtime(tmp_path, elicit_fn=elicit)
    out = await rt.clarify("which db?")
    assert out["questions"][0]["question"] == "Budget?"
    assert out["estimate"]["confirm_required"] is True


def _stages(events: list[dict[str, Any]]) -> list[str]:
    return [str(event.get("stage")) for event in events]


def _first(stages: list[str], name: str) -> int:
    try:
        return stages.index(name)
    except ValueError as exc:
        raise AssertionError(f"stage {name!r} missing in {stages}") from exc


def test_last_verdict_ignores_start_and_error() -> None:
    assert last_verdict([{"stage": "start"}, {"stage": "error", "error": "401"}]) is None
    payload = {"decision": "use postgres", "transcript_id": "abc"}
    found = last_verdict(
        [
            {"stage": "start"},
            {"stage": "route", "protocol": "vote"},
            {"stage": "positions"},
            {"stage": "verdict", "verdict": payload},
        ]
    )
    assert found == payload


def test_verdict_and_transcript_roundtrip(tmp_path) -> None:
    """create() пишет start; route не обязан быть events[0]; id сохраняется."""
    rt, _ = _runtime(tmp_path)
    tid = rt.transcripts.create({"question": "which db?"})
    rt.transcripts.append(tid, {"stage": "route", "protocol": "vote"})
    rt.transcripts.append(tid, {"stage": "positions", "n": 3})
    dumped = _verdict(tid).model_dump()
    rt.transcripts.append(tid, {"stage": "verdict", "verdict": dumped})
    found = rt.verdict(tid)
    assert found["status"] == "verdict"
    assert found["verdict"]["decision"] == "use postgres"
    assert found["verdict"]["transcript_id"] == tid
    raw = rt.transcript(tid, fmt="json")
    assert raw["transcript_id"] == tid
    events = raw["events"]
    stages = _stages(events)
    assert stages[0] == "start"
    assert events[0]["question"] == "which db?"
    assert _first(stages, "start") < _first(stages, "route") < _first(stages, "verdict")
    assert "positions" in stages
    route = next(event for event in events if event["stage"] == "route")
    assert route["protocol"] == "vote"
    md = rt.transcript(tid, fmt="md")
    assert "хроніка" in md["markdown"]
    assert tid in md["markdown"]
    assert rt.verdict("missing")["error"] == "not_found"


def test_error_transcript_is_not_a_successful_verdict(tmp_path) -> None:
    """Провайдер упал: start + error. zhoda_verdict не маскирует это под успех."""
    rt, _ = _runtime(tmp_path)
    tid = rt.transcripts.create({"question": "which db?"})
    rt.transcripts.append(
        tid,
        {"stage": "error", "error_type": "ZhodaProviderError", "error": "401: expired"},
    )
    found = rt.verdict(tid)
    assert found.get("status") != "verdict"
    assert "verdict" not in found
    assert found["error"] == "not_found"
    raw = rt.transcript(tid, fmt="json")
    assert raw["transcript_id"] == tid
    stages = _stages(raw["events"])
    assert stages[0] == "start"
    assert stages[-1] == "error"
    assert "verdict" not in stages
    assert raw["events"][-1]["error_type"] == "ZhodaProviderError"


def test_reputation_filters_domain(tmp_path) -> None:
    rt, _ = _runtime(tmp_path)
    matrix = DomainEloMatrix()
    matrix.record_match("m1", "m2", {Domain.CODE_ARCHITECTURE: 1.0})
    rt.reputation.save(matrix)
    full = rt.reputation_report()
    assert "m1" in full["ratings"]
    sliced = rt.reputation_report("code_architecture")
    assert sliced["domain"] == "code_architecture"
    assert "m1" in sliced["ratings"]
    bad = rt.reputation_report("astrology")
    assert bad["error"] == "unknown_domain"


@pytest.mark.asyncio
async def test_remote_core_url_is_honest(tmp_path) -> None:
    rt, engine = _runtime(tmp_path, remote="http://localhost:8000")
    out = await rt.deliberate("q", confirm=True)
    assert out["error"] == "remote_core_unwired"
    assert engine.calls == []
