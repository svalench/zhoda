"""Хроніка не бывает пустой: start при create, error при падении провайдера."""

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
