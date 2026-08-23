"""События стадий deliberation. CLI рисует спиннер; engine/MCP не зависят от Rich."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProgressEvent:
    stage: str
    message: str
    done: bool = False
