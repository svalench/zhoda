"""cache_mode=fresh не открывает занятый sqlite. Replay/resume — да."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from zhoda_core.benchmarks.cache_guard import (
    FreshCacheOccupiedError,
    ensure_fresh_cache,
    sqlite_cache_rows,
)
from zhoda_core.benchmarks.checkpoint import CheckpointStore


def _seed(path: Path, n: int = 1) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, v TEXT)")
    for i in range(n):
        conn.execute("INSERT INTO cache (k, v) VALUES (?, ?)", (f"k{i}", "v"))
    conn.commit()
    conn.close()


def test_fresh_refuses_occupied_sqlite(tmp_path) -> None:
    path = tmp_path / "c.db"
    _seed(path, 3)
    assert sqlite_cache_rows(path) == 3
    with pytest.raises(FreshCacheOccupiedError, match="3 rows"):
        ensure_fresh_cache(path, cache_mode="fresh", resume=False)


def test_replay_allows_occupied_sqlite(tmp_path) -> None:
    path = tmp_path / "c.db"
    _seed(path)
    ensure_fresh_cache(path, cache_mode="replay", resume=False)


def test_fresh_resume_allows_occupied_sqlite(tmp_path) -> None:
    path = tmp_path / "c.db"
    _seed(path)
    ensure_fresh_cache(path, cache_mode="fresh", resume=True)


def test_fresh_empty_or_missing_is_ok(tmp_path) -> None:
    missing = tmp_path / "no.db"
    empty = tmp_path / "empty.db"
    empty.write_bytes(b"")
    ensure_fresh_cache(missing, cache_mode="fresh")
    ensure_fresh_cache(empty, cache_mode="fresh")
    assert sqlite_cache_rows(missing) == 0
    assert sqlite_cache_rows(empty) == 0


def test_build_live_arms_fresh_refuses_preseeded_arm_file(tmp_path, monkeypatch) -> None:
    from zhoda_core.benchmarks import engine as engmod

    yaml = tmp_path / "z.yaml"
    yaml.write_text(
        "council: [a, b, c]\njudges: [j1, j2]\nrouter_classifiers: [j1, j2]\nchairman: a\n",
        encoding="utf-8",
    )
    base = tmp_path / "c.db"
    _seed(tmp_path / "c-zhoda.db")
    monkeypatch.setattr(engmod, "make_provider", lambda cfg, **kwargs: type("P", (), {})())
    monkeypatch.setattr(engmod, "make_engine", lambda cfg, provider, **kwargs: provider)
    with pytest.raises(FreshCacheOccupiedError):
        engmod.build_live_arms(
            yaml, cache_path=str(base), modes=("zhoda",), cache_mode="fresh",
        )


def test_build_live_arms_fresh_resume_opens_preseeded(tmp_path, monkeypatch) -> None:
    from zhoda_core.benchmarks import engine as engmod

    yaml = tmp_path / "z.yaml"
    yaml.write_text(
        "council: [a, b, c]\njudges: [j1, j2]\nrouter_classifiers: [j1, j2]\nchairman: a\n",
        encoding="utf-8",
    )
    seen: list[str] = []

    def fake_provider(cfg: dict, **kwargs: object) -> object:
        del kwargs
        seen.append(str(cfg.get("cache_path")))
        return type("P", (), {})()

    monkeypatch.setattr(engmod, "make_provider", fake_provider)
    monkeypatch.setattr(engmod, "make_engine", lambda cfg, provider, **kwargs: provider)
    _seed(tmp_path / "c-zhoda.db")
    arms = engmod.build_live_arms(
        yaml,
        cache_path=str(tmp_path / "c.db"),
        modes=("zhoda",),
        cache_mode="fresh",
        resume=True,
    )
    assert "zhoda" in arms
    assert seen


def test_checkpoint_has_any_terminal(tmp_path) -> None:
    store = CheckpointStore(tmp_path / "ckpt.jsonl")
    assert store.has_any_terminal() is False
    store.put("k", {"status": "ok", "result": {}})
    assert store.has_any_terminal() is True
