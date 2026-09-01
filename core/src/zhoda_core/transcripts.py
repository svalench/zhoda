"""Transcript persistence (хроніка).

Protocol invariant: every deliberation is persisted BEFORE the verdict is
returned. JSONL, one event per line — append-only and auditable.
"""

import json
import time
import uuid
from pathlib import Path
from typing import Any


class TranscriptStore:
    def __init__(self, directory: str = "transcripts") -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def create(self, extra: dict[str, Any] | None = None) -> str:
        """Открыть хроніку: сразу `start`, файл никогда не пустой."""
        transcript_id = uuid.uuid4().hex[:12]
        event: dict[str, Any] = {"ts": time.time(), **(extra or {}), "stage": "start"}
        path = self.dir / f"{transcript_id}.jsonl"
        path.write_text(json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8")
        return transcript_id

    def append(self, transcript_id: str, event: dict[str, Any]) -> None:
        path = self.dir / f"{transcript_id}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": time.time(), **event}, ensure_ascii=False) + "\n")
            handle.flush()

    def read(self, transcript_id: str) -> list[dict[str, Any]]:
        path = self.dir / f"{transcript_id}.jsonl"
        with path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
