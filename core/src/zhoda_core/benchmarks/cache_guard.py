"""cache_mode=fresh не переигрывает чужой sqlite.

Intra-question hits после пустого файла — норма. Занятый файл на старте — нет.
Resume (checkpoint с terminal row) может держать тот же sqlite.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


class FreshCacheOccupiedError(ValueError):
    """fresh run увидел непустой sqlite без resume — это replay, не live."""


def sqlite_cache_rows(path: str | Path) -> int:
    """Сколько ключей в cache. Нет файла / нет таблицы → 0."""
    target = Path(path)
    if not target.exists() or target.stat().st_size == 0:
        return 0
    conn = sqlite3.connect(f"file:{target.resolve()}?mode=ro", uri=True)
    try:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cache'"
        ).fetchone()
        if table is None:
            return 0
        row = conn.execute("SELECT COUNT(*) FROM cache").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.DatabaseError:
        return 0
    finally:
        conn.close()


def ensure_fresh_cache(
    path: str | Path,
    *,
    cache_mode: str,
    resume: bool = False,
) -> None:
    """replay — ок. fresh + resume — ок. fresh + rows — отказ."""
    if cache_mode != "fresh" or resume:
        return
    n = sqlite_cache_rows(path)
    if n > 0:
        raise FreshCacheOccupiedError(
            f"cache_mode=fresh refuses non-empty sqlite {path} ({n} rows); "
            "delete the file or pass cache_mode=replay"
        )


def replayed_without_http(requests: int, cache_hits: int) -> bool:
    """$0 + cache_hits без HTTP — replay, не live spend."""
    return cache_hits > 0 and requests == 0
