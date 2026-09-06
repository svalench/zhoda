"""Checkpoint/resume: case × arm × replicate × spec_hash.

Сбой не становится новым наблюдением. Changed spec не читает чужой cache.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


class CheckpointConflict(ValueError):
    """Тот же ключ, другой payload — нельзя молча дублировать."""


def checkpoint_key(case_id: str, arm: str, replicate_id: int, spec_hash: str) -> str:
    return f"{case_id}\x1f{arm}\x1f{replicate_id}\x1f{spec_hash}"


@dataclass
class CheckpointStore:
    path: Path

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")

    def _rows(self) -> Iterator[dict[str, Any]]:
        text = self.path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.strip():
                yield json.loads(line)

    def get(self, key: str) -> dict[str, Any] | None:
        found: dict[str, Any] | None = None
        for row in self._rows():
            if row.get("key") == key:
                found = row
        return found

    def put(self, key: str, record: dict[str, Any]) -> None:
        prior = self.get(key)
        payload = {"key": key, **record}
        if prior is not None:
            prior_body = {k: v for k, v in prior.items() if k != "ts"}
            new_body = {k: v for k, v in payload.items() if k != "ts"}
            if prior_body != new_body:
                raise CheckpointConflict(f"checkpoint {key!r} already has a different record")
            return  # idempotent
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def count_rows(self) -> int:
        return sum(1 for _ in self._rows())

    def matching_spec_terminal_rows(self, spec_hash: str) -> int:
        """Terminal rows, чей ключ заканчивается этим spec_hash."""
        from .cache_guard import CHECKPOINT_TERMINAL

        n = 0
        for row in self._rows():
            if str(row.get("status") or "") not in CHECKPOINT_TERMINAL:
                continue
            key = str(row.get("key") or "")
            parts = key.split("\x1f")
            if len(parts) >= 4 and parts[-1] == spec_hash:
                n += 1
        return n

    def has_any_terminal(self) -> bool:
        """Есть ли хотя бы один завершённый attempt — признак resume того же run."""
        from .cache_guard import CHECKPOINT_TERMINAL

        return any(str(row.get("status") or "") in CHECKPOINT_TERMINAL for row in self._rows())

    def has_terminal(self, key: str) -> bool:
        from .cache_guard import CHECKPOINT_TERMINAL

        row = self.get(key)
        if row is None:
            return False
        return str(row.get("status") or "") in CHECKPOINT_TERMINAL
