"""cache_mode=fresh не открывает занятый sqlite. Resume требует checkpoint."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from zhoda_core.benchmarks.cache_guard import (
    CacheNotFreshError,
    ResumeRequiresCheckpointError,
    apply_allow_resume,
    cost_status_for,
    ensure_fresh_cache,
    ensure_resume_checkpoint,
    served_from_cache,
    sqlite_cache_rows,
)
from zhoda_core.benchmarks.checkpoint import CheckpointStore, checkpoint_key
from zhoda_core.benchmarks.metrics import summarize
from zhoda_core.benchmarks.runner import (
    MATCH_REQUEST,
    MODE_MAJORITY,
    MODE_SHORT_REVIEW,
    MODE_ZHODA,
    CaseResult,
    ComparativeRunner,
    EngineOutcome,
)


def _seed(path: Path, n: int = 1) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, v TEXT)")
    for i in range(n):
        conn.execute("INSERT INTO cache (k, v) VALUES (?, ?)", (f"k{i}", "v"))
    conn.commit()
    conn.close()


def test_fresh_refuses_occupied_sqlite(tmp_path: Path) -> None:
    path = tmp_path / "c.db"
    _seed(path, 3)
    assert sqlite_cache_rows(path) == 3
    with pytest.raises(CacheNotFreshError, match="3 rows"):
        ensure_fresh_cache(path, cache_mode="fresh")


def test_fresh_empty_or_missing_is_ok(tmp_path: Path) -> None:
    missing = tmp_path / "no.db"
    empty = tmp_path / "empty.db"
    empty.write_bytes(b"")
    ensure_fresh_cache(missing, cache_mode="fresh")
    ensure_fresh_cache(empty, cache_mode="fresh")
    assert sqlite_cache_rows(missing) == 0
    assert sqlite_cache_rows(empty) == 0


def test_replay_and_resume_allow_occupied_sqlite(tmp_path: Path) -> None:
    path = tmp_path / "c.db"
    _seed(path)
    ensure_fresh_cache(path, cache_mode="replay")
    ensure_fresh_cache(path, cache_mode="resume")


def test_fresh_resume_flag_does_not_bypass_guard(tmp_path: Path) -> None:
    """`--allow-resume` меняет mode на resume; fresh + occupied всегда ошибка."""
    path = tmp_path / "c.db"
    _seed(path)
    with pytest.raises(CacheNotFreshError):
        ensure_fresh_cache(path, cache_mode="fresh")
    assert apply_allow_resume("fresh", True) == "resume"


def test_build_live_arms_fresh_refuses_preseeded_arm_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from zhoda_core.benchmarks import engine as engmod

    yaml = tmp_path / "z.yaml"
    yaml.write_text(
        "council: [a, b, c]\njudges: [j1, j2]\nrouter_classifiers: [j1, j2]\nchairman: a\n",
        encoding="utf-8",
    )
    _seed(tmp_path / "c-zhoda.db")
    monkeypatch.setattr(engmod, "make_provider", lambda cfg, **kwargs: type("P", (), {})())
    monkeypatch.setattr(engmod, "make_engine", lambda cfg, provider, **kwargs: provider)
    with pytest.raises(CacheNotFreshError):
        engmod.build_live_arms(
            yaml,
            cache_path=str(tmp_path / "c.db"),
            modes=("zhoda",),
            cache_mode="fresh",
        )


def test_build_live_arms_resume_opens_preseeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
        cache_mode="resume",
    )
    assert "zhoda" in arms
    assert seen


def test_resume_without_checkpoint_errors(tmp_path: Path) -> None:
    missing = tmp_path / "no-ckpt.jsonl"
    with pytest.raises(ResumeRequiresCheckpointError, match="existing checkpoint"):
        ensure_resume_checkpoint(missing, spec_hash="abc" * 8)
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ResumeRequiresCheckpointError, match="existing checkpoint"):
        ensure_resume_checkpoint(empty, spec_hash="abc" * 8)
    store = CheckpointStore(tmp_path / "ckpt.jsonl")
    store.put("unrelated", {"status": "ok", "result": {}})
    with pytest.raises(ResumeRequiresCheckpointError, match="no terminal rows"):
        ensure_resume_checkpoint(store.path, spec_hash="deadbeef" * 4)


def test_resume_with_matching_spec_hash(tmp_path: Path) -> None:
    spec_hash = "cafe" * 8
    store = CheckpointStore(tmp_path / "ckpt.jsonl")
    key = checkpoint_key("evd-001", "zhoda", 0, spec_hash)
    store.put(key, {"status": "ok", "result": {}})
    assert ensure_resume_checkpoint(store.path, spec_hash) == 1


def test_served_from_cache_row_excluded_from_mean_usd() -> None:
    assert served_from_cache(0, 53) is True
    assert cost_status_for(0, 53, "exact") == "cached"
    assert served_from_cache(32, 24) is False
    live = CaseResult(
        case_id="a",
        suite="pilot",
        kind="evidence",
        mode="zhoda",
        decision="x",
        requests=10,
        usd=0.04,
        cache_hits=2,
        cost_status="exact",
    )
    cached = CaseResult(
        case_id="b",
        suite="pilot",
        kind="evidence",
        mode="zhoda",
        decision="x",
        requests=0,
        usd=0.0,
        cache_hits=53,
        served_from_cache=True,
        cost_status="cached",
    )
    summary = summarize([live, cached])
    assert summary["zhoda"]["avg_usd"] == pytest.approx(0.04)
    assert summary["zhoda"]["n_cached"] == 1.0
    assert summary["zhoda"]["n_exact_cost"] == 1.0


def test_cost_match_excludes_cached() -> None:
    from zhoda_core.benchmarks.matching import STATUS_UNMATCHED, cost_match

    miss = cost_match(
        actual_usd=0.0,
        target_usd=0.04,
        actual_tokens=0,
        target_tokens=None,
        cost_status="cached",
    )
    assert miss.status == STATUS_UNMATCHED


def test_mixed_cache_live_pair_not_cost_comparable() -> None:
    from zhoda_core.benchmarks.datasets import builtin_cases

    class Arm:
        def __init__(self, requests: int, cache_hits: int, usd: float) -> None:
            self.requests = requests
            self.cache_hits = cache_hits
            self.usd = usd

        async def deliberate(self, **kwargs: object) -> EngineOutcome:
            del kwargs
            return EngineOutcome(
                decision="ok",
                requests=self.requests,
                cache_hits=self.cache_hits,
                usd=self.usd,
                engine_usage={"usd_status": "exact", "usd_known": True},
            )

    runner = ComparativeRunner(
        arms={
            MODE_ZHODA: Arm(10, 0, 0.03),  # type: ignore[dict-item]
            MODE_SHORT_REVIEW: Arm(0, 12, 0.0),  # type: ignore[dict-item]
            MODE_MAJORITY: Arm(8, 0, 0.01),  # type: ignore[dict-item]
        },
        compare_modes=(MODE_ZHODA, MODE_SHORT_REVIEW, MODE_MAJORITY),
        tables=(MATCH_REQUEST,),
    )
    case = builtin_cases("sycophancy")[0]
    rows = asyncio.run(runner.run_suite([case], ["m1", "m2", "m3"], mode="compare", rounds=1))
    assert any(r.served_from_cache for r in rows)
    assert all(r.cost_comparable is False for r in rows)


def test_plan_amendment_does_not_change_frozen_identity() -> None:
    import hashlib
    import json

    from zhoda_core.eval.pilot import FROZEN_MANIFEST, PLAN_MD

    frozen = json.loads(FROZEN_MANIFEST.read_text(encoding="utf-8"))
    text = PLAN_MD.read_text(encoding="utf-8")
    idx = text.find("\n## Поправка 1")
    assert idx > 0
    identity = text[: idx + 1]
    assert hashlib.sha256(identity.encode("utf-8")).hexdigest() == frozen["plan_hash"]


def test_resume_spec_hash_matches_fresh(tmp_path: Path) -> None:
    from zhoda_core.benchmarks.datasets import builtin_cases
    from zhoda_core.benchmarks.spec import resolve_run_spec

    kwargs = dict(
        cases=builtin_cases("sycophancy")[:1],
        yaml_cfg={"council": ["a", "b"], "judges": ["j1"], "chairman": "a", "rounds_cap": 4},
        models_override=None,
        rounds_override=None,
        budget_override=None,
        cache_path=str(tmp_path / "c.db"),
        isolate_cache=True,
        replicate_id=0,
        clarify_mode="no-clarify",
        dry_run=True,
    )
    fresh = resolve_run_spec(**kwargs, cache_mode="fresh")  # type: ignore[arg-type]
    resume = resolve_run_spec(**kwargs, cache_mode="resume")  # type: ignore[arg-type]
    assert resume.cache_mode == "resume"
    assert resume.spec_hash == fresh.spec_hash


def test_fresh_refuses_real_pilot_arm_cache() -> None:
    root = Path(__file__).resolve().parents[2]
    path = root / "docs" / "live-runs" / "2026-09-06-g-pilot" / "cache-zhoda.db"
    if not path.exists():
        pytest.skip("pilot cache-zhoda.db is not on disk")
    n = sqlite_cache_rows(path)
    assert n > 0
    with pytest.raises(CacheNotFreshError, match=f"{n} rows"):
        ensure_fresh_cache(path, cache_mode="fresh")


def test_run_live_g_fresh_does_not_auto_resume_from_checkpoint() -> None:
    src = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "live-runs"
        / "2026-09-06-g-pilot-v2"
        / "run_live_g.py"
    )
    text = src.read_text(encoding="utf-8")
    assert "has_any_terminal" not in text
    assert "allow_resume" in text
    assert "ensure_fresh_cache" in text
