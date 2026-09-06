"""cache_mode=fresh не переигрывает чужой sqlite.

Intra-question hits после пустого файла — норма. Занятый файл на старте — нет.
`--allow-resume` переводит режим в resume и требует checkpoint с тем же spec_hash.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

COST_EXACT = "exact"
COST_CACHED = "cached"
COST_PARTIAL = "partial"

CHECKPOINT_TERMINAL = frozenset({"ok", "failed", "ungraded", "skipped", "infeasible"})


class CacheNotFreshError(ValueError):
    """fresh-прогон увидел непустой sqlite — это replay, не live spend."""


class ResumeRequiresCheckpointError(ValueError):
    """resume без checkpoint / без matching spec_hash."""


# Старое имя из тонкого P3; то же исключение.
FreshCacheOccupiedError = CacheNotFreshError


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


def jsonl_row_count(path: str | Path) -> int:
    """Строки JSONL. Нет файла → 0, файл не создаём."""
    target = Path(path)
    if not target.exists() or target.stat().st_size == 0:
        return 0
    return sum(1 for line in target.read_text(encoding="utf-8").splitlines() if line.strip())


def served_from_cache(requests: int, cache_hits: int) -> bool:
    """HTTP не было, ответы взяты из sqlite."""
    return int(requests) == 0 and int(cache_hits) > 0


def replayed_without_http(requests: int, cache_hits: int) -> bool:
    """Алиас served_from_cache: $0 + hits без HTTP."""
    return served_from_cache(requests, cache_hits)


def cost_status_for(
    requests: int,
    cache_hits: int,
    usd_status: str = COST_EXACT,
) -> str:
    """exact — live с известным USD; cached — целиком из sqlite; иначе partial."""
    if served_from_cache(requests, cache_hits):
        return COST_CACHED
    token = str(usd_status or COST_EXACT).split(".")[-1].lower()
    if token in {COST_PARTIAL, "unknown"}:
        return COST_PARTIAL
    return COST_EXACT


def ensure_fresh_cache(path: str | Path, *, cache_mode: str) -> None:
    """replay/resume — ок. fresh + rows — отказ. resume не прячется под fresh."""
    if cache_mode != "fresh":
        return
    n = sqlite_cache_rows(path)
    if n > 0:
        raise CacheNotFreshError(
            f"cache_mode=fresh refuses non-empty sqlite {path} ({n} rows); "
            "delete the file or pass --allow-resume (cache_mode=resume)"
        )


def ensure_resume_checkpoint(path: str | Path, spec_hash: str) -> int:
    """resume требует существующий JSONL с terminal rows того же spec_hash."""
    target = Path(path)
    if not spec_hash:
        raise ResumeRequiresCheckpointError(
            "cache_mode=resume requires spec_hash to match checkpoint keys"
        )
    if not target.exists() or target.stat().st_size == 0:
        raise ResumeRequiresCheckpointError(
            f"cache_mode=resume requires existing checkpoint {target} "
            f"with spec_hash={spec_hash[:16]}"
        )
    from .checkpoint import CheckpointStore

    store = CheckpointStore(target)
    n = store.matching_spec_terminal_rows(spec_hash)
    if n == 0:
        raise ResumeRequiresCheckpointError(
            f"checkpoint {target} has no terminal rows for spec_hash={spec_hash[:16]}"
        )
    return n


def apply_allow_resume(cache_mode: str, allow_resume: bool) -> str:
    """Флаг --allow-resume переводит fresh → resume. replay не трогаем."""
    if not allow_resume:
        return cache_mode
    if cache_mode == "replay":
        raise ResumeRequiresCheckpointError("--allow-resume is not valid with --cache-mode replay")
    return "resume"
