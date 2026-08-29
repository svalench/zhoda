"""Рендер хронікі в JSON/markdown. Вердикт — последний event stage=verdict."""

from __future__ import annotations

import json
from typing import Any


def last_verdict(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("stage") != "verdict":
            continue
        inner = event.get("verdict")
        if isinstance(inner, dict):
            return inner
    return None


def render_transcript_md(transcript_id: str, events: list[dict[str, Any]]) -> str:
    lines = [f"# хроніка `{transcript_id}`", ""]
    for event in events:
        stage = str(event.get("stage", "event"))
        lines.append(f"## {stage}")
        payload = {k: v for k, v in event.items() if k not in {"stage", "ts"}}
        lines.append("```json")
        lines.append(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        lines.append("```")
        lines.append("")
    return "\n".join(lines)
