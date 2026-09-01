"""Хроніка не бывает пустой: start при create, error при падении провайдера."""

from pathlib import Path

import pytest

from zhoda_core.engine import ZhodaEngine
from zhoda_core.providers.openrouter import ZhodaProviderError
from zhoda_core.transcripts import TranscriptStore

from .test_e2e import CLASSIFIERS, COUNCIL, JUDGES, ScriptedProvider, make_engine


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
    assert events[0]["stage"] == "start"
    assert events[0]["question"] == "which db?"
    assert events[-1]["stage"] == "error"
    assert events[-1]["error_type"] == "ZhodaProviderError"
    assert "401" in events[-1]["error"]


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
