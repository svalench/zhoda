"""Ближайший .env вверх до git-корня. Существующие переменные не перезаписываем."""

from __future__ import annotations

import os
from pathlib import Path


def find_env_file(start: Path | None = None) -> Path | None:
    start = (start or Path.cwd()).resolve()
    for path in [start, *start.parents]:
        candidate = path / ".env"
        if candidate.is_file():
            return candidate
        if (path / ".git").exists():
            break
    return None


def parse_dotenv(text: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            parsed[key] = value
    return parsed


def load_zhoda_env(start: Path | None = None) -> Path | None:
    found = find_env_file(start)
    if found is None:
        return None
    parsed = parse_dotenv(found.read_text(encoding="utf-8-sig"))
    for key, value in parsed.items():
        if key not in os.environ:
            os.environ[key] = value
    return found
