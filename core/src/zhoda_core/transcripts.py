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

    def create(self) -> str:
        transcript_id = uuid.uuid4().hex[:12]
        (self.dir / f"{transcript_id}.jsonl").touch()
        return transcript_id

    def append(self, transcript_id: str, event: dict[str, Any]) -> None:
        path = self.dir / f"{transcript_id}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": time.time(), **event}, ensure_ascii=False) + "\n")

    def read(self, transcript_id: str) -> list[dict[str, Any]]:
        path = self.dir / f"{transcript_id}.jsonl"
        with path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
