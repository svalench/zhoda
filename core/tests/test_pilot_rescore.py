"""Offline rescore v2: FakeProvider, source report не трогаем. Без сети."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from zhoda_core.eval.gold import load_gold
from zhoda_core.eval.grading import GRADER_VERSION
from zhoda_core.eval.pilot import GOLD_JSONL, load_public_cases
from zhoda_core.eval.pilot_rescore import (
    SCHEMA_RESCORE_V2,
    JudgeOnlyProvider,
    annotate_v1_fields,
    dropped_cases,
    engine_cost_status,
    judge_roster,
    paired_fully_graded,
    refresh_engine_cost_tables,
    rescore_report_v2,
    resumed_from_cache,
)
from zhoda_core.models import CostReport
from zhoda_core.providers.openrouter import BudgetExceededError

KEEP_INDEX = (
    "Recommended (majority at cap, not zhoda): The index `idx_orders_created_at` "
    "should be retained because while it's unused in the current query plan due to "
    "the specific `id=1` filter, its potential value for broader query patterns "
    "and the negligible cost of retention on a small table outweigh the immediate "
    "lack of use.\nDissent:\nIndex Droppers: drop it."
)


class FakeProvider:
    def __init__(self, payload: dict[str, object], *, hits: int = 0) -> None:
        self.payload = dict(payload)
        self.prompts: list[str] = []
        self.models: list[str] = []
        self.calls = 0
        self.hits = hits

    async def ask_json(
        self, model: str, prompt: str, cache_key: str | None = None, **kwargs: object
    ) -> dict:
        del cache_key, kwargs
        self.models.append(model)
        self.prompts.append(prompt)
        self.calls += 1
        return dict(self.payload)

    def question_report(self) -> CostReport:
        if self.hits:
            return CostReport(requests=0, cache_hits=self.hits * self.calls, usd=0.0)
        return CostReport(
            requests=self.calls,
            tokens_in=self.calls * 8,
            usd=0.001 * self.calls,
            cache_hits=0,
        )


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def _triple(case_id: str, *, failed_maj: bool = False) -> dict:
    zhoda = {
        "case_id": case_id,
        "mode": "zhoda",
        "kind": "evidence_required",
        "decision": KEEP_INDEX,
        "coverage_status": "ok",
        "match": "request",
        "usd": 0.01,
        "requests": 4,
        "action_correct": False,
        "chosen_action": "paraphrase",
        "appropriate_abstention": True,
    }
    sr = dict(
        zhoda,
        mode="short_review",
        usd=0.0,
        requests=0,
        cache_hits=12,
        cost_status="cached",
    )
    maj = dict(
        zhoda,
        mode="majority",
        usd=0.002,
        requests=3,
        cache_hits=0,
        action_correct=True,
    )
    if failed_maj:
        maj["coverage_status"] = "failed"
        maj["decision"] = ""
    return {"schema": "zhoda.eval.live_g.v1", "n_cases": 1, "results": [zhoda, sr, maj]}


def test_live_g_v1_report_hash_frozen() -> None:
    import hashlib

    from zhoda_core.eval.pilot_rescore import SOURCE_REPORT_SHA256

    live = Path(__file__).resolve().parents[2] / "docs" / "live-runs"
    report = live / "2026-09-06-g-pilot" / "report.json"
    digest = hashlib.sha256(report.read_bytes()).hexdigest()
    assert digest == SOURCE_REPORT_SHA256
    v1 = live / "2026-09-06-g-pilot"
    assert not (v1 / "report-v2.json").exists()
    assert not (v1 / "rescore_v2.py").exists()
    assert not (v1 / "rescore-v2").exists()
    assert (live / "2026-09-06-g-rescore-v2" / "report-v2.json").is_file()
    assert (live / "2026-09-06-g-rescore-v2" / "rescore_v2.py").is_file()
    v2_runner = live / "2026-09-06-g-pilot-v2" / "run_live_g.py"
    assert v2_runner.is_file()
    assert "has_any_terminal" not in v2_runner.read_text(encoding="utf-8")


def test_v1_live_g_tree_matches_9abbe24() -> None:
    """v1 = 9abbe24: WT, индекс, и `git diff 9abbe24..HEAD` (или индекс вернул blob)."""
    import subprocess

    root = Path(__file__).resolve().parents[2]
    v1 = "docs/live-runs/2026-09-06-g-pilot/"
    base = "9abbe24"

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)

    wt = git("diff", "--stat", base, "--", v1)
    cached = git("diff", "--stat", "--cached", base, "--", v1)
    head = git("diff", "--stat", f"{base}..HEAD", "--", v1)
    extra = git("ls-files", "-o", "--exclude-standard", "--", v1)
    failed = next(
        (item for item in (wt, cached, head, extra) if item.returncode != 0),
        None,
    )
    if failed is not None:
        pytest.skip(failed.stderr.strip() or "git diff failed")
    assert wt.stdout.strip() == ""
    assert cached.stdout.strip() == ""
    assert extra.stdout.strip() == ""
    if not head.stdout.strip():
        return
    names = git("diff", "--name-only", f"{base}..HEAD", "--", v1)
    assert names.returncode == 0
    for rel in names.stdout.splitlines():
        base_blob = git("show", f"{base}:{rel}")
        index_blob = git("show", f":{rel}")
        assert base_blob.returncode == 0, rel
        assert index_blob.returncode == 0, rel
        assert index_blob.stdout == base_blob.stdout, rel
    base_names = set(git("ls-tree", "-r", "--name-only", base, "--", v1).stdout.splitlines())
    head_names = set(git("ls-tree", "-r", "--name-only", "HEAD", "--", v1).stdout.splitlines())
    index_names = set(git("ls-files", "--", v1).stdout.splitlines())
    for rel in head_names - base_names:
        assert rel not in index_names, rel
    for rel in base_names - head_names:
        assert rel in index_names, rel


def test_judge_roster_refuses_chairman_and_council() -> None:
    with pytest.raises(ValueError, match="chairman"):
        judge_roster({"judges": ["a"], "chairman": "a", "council": ["b"]})
    with pytest.raises(ValueError, match="council"):
        judge_roster({"judges": ["b"], "chairman": "a", "council": ["b"]})
    model, overlap = judge_roster(
        {
            "judges": ["j1", "j2"],
            "chairman": "a",
            "council": ["a", "b"],
            "router_classifiers": ["j1"],
        }
    )
    assert model == "j1"
    assert overlap == ("protocol_judge", "classifier")


def test_refresh_engine_cost_tables_sets_n_cached() -> None:
    payload = {
        "schema": "zhoda.eval.live_g.rescore.v2",
        "results": [
            {
                "case_id": "evd-001",
                "mode": "zhoda",
                "kind": "evidence_required",
                "coverage_status": "ok",
                "grade_status": "graded",
                "action_correct_v2": True,
                "usd": 0.0,
                "requests": 0,
                "cache_hits": 53,
            },
            {
                "case_id": "evd-001",
                "mode": "short_review",
                "kind": "evidence_required",
                "coverage_status": "ok",
                "grade_status": "graded",
                "action_correct_v2": True,
                "usd": 0.01,
                "requests": 4,
                "cache_hits": 0,
            },
            {
                "case_id": "evd-001",
                "mode": "majority",
                "kind": "evidence_required",
                "coverage_status": "ok",
                "grade_status": "graded",
                "action_correct_v2": True,
                "usd": 0.002,
                "requests": 3,
                "cache_hits": 0,
            },
        ],
    }
    out = refresh_engine_cost_tables(payload)
    assert out["primary"]["n_cached_oxford"] == 1
    assert out["primary"]["mean_usd_oxford"] is None
    assert out["primary"]["mean_usd_short_review"] == pytest.approx(0.01)
    assert out["pairs"][0]["oxford_usd"] is None
    assert out["results"][0]["engine_cost_status"] == "cached"


def test_committed_report_v2_primary_excludes_cached_usd() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "live-runs"
        / "2026-09-06-g-rescore-v2"
        / "report-v2.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    primary = payload["primary"]
    assert "n_cached_oxford" in primary
    assert "n_cached_short_review" in primary
    pairs = {p["case_id"]: p for p in payload["pairs"]}
    evd = pairs.get("evd-001")
    if evd is not None:
        assert evd["oxford_usd"] is None
        assert evd["oxford_cached"] is True


def test_resumed_from_cache_flag() -> None:
    assert resumed_from_cache({"requests": 0, "cache_hits": 3}) is True
    assert resumed_from_cache({"requests": 1, "cache_hits": 3}) is False
    assert resumed_from_cache({"requests": 0, "cache_hits": 0}) is False


def test_engine_cost_status_excludes_cached_from_mean() -> None:
    assert engine_cost_status({"requests": 0, "cache_hits": 53, "usd": 0.0}) == "cached"
    assert engine_cost_status({"requests": 10, "cache_hits": 2, "usd": 0.04}) == "exact"
    scored = [
        {
            "case_id": "evd-001",
            "mode": "zhoda",
            "kind": "evidence_required",
            "coverage_status": "ok",
            "grade_status": "graded",
            "action_correct_v2": True,
            "usd": 0.04,
            "requests": 10,
            "cache_hits": 0,
            "cost_status": "exact",
        },
        {
            "case_id": "evd-001",
            "mode": "short_review",
            "kind": "evidence_required",
            "coverage_status": "ok",
            "grade_status": "graded",
            "action_correct_v2": True,
            "usd": 0.0,
            "requests": 0,
            "cache_hits": 12,
            "cost_status": "cached",
        },
        {
            "case_id": "evd-001",
            "mode": "majority",
            "kind": "evidence_required",
            "coverage_status": "ok",
            "grade_status": "graded",
            "action_correct_v2": True,
            "usd": 0.01,
            "requests": 8,
            "cache_hits": 0,
            "cost_status": "exact",
        },
    ]
    primary = paired_fully_graded(scored)
    assert primary["n_paired"] == 1
    assert primary["mean_usd_oxford"] == pytest.approx(0.04)
    assert primary["mean_usd_short_review"] is None
    assert primary["n_cached_short_review"] == 1
    assert primary["n_cached_oxford"] == 0
    assert primary["n_exact_cost_oxford"] == 1
    assert primary["n_exact_cost_short_review"] == 0


def test_judge_only_refuses_council_model() -> None:
    inner = FakeProvider({"picked_id": "Keep the index", "committed": True, "quote": "x"})
    wrapped = JudgeOnlyProvider(inner, "openai/gpt-4o-mini")

    async def _bad() -> None:
        await wrapped.ask_json("openai/gpt-4.1-mini", "prompt")

    with pytest.raises(ValueError, match="non-judge"):
        _run(_bad())
    assert inner.calls == 0


def test_judge_only_enforces_cap() -> None:
    inner = FakeProvider({"picked_id": "Keep the index", "committed": True, "quote": "x"})
    wrapped = JudgeOnlyProvider(inner, "openai/gpt-4o-mini", cap_usd=0.0)

    async def _over() -> None:
        inner.calls = 1
        await wrapped.ask_json("openai/gpt-4o-mini", "prompt")

    with pytest.raises(BudgetExceededError):
        _run(_over())


def test_annotate_keeps_v1_beside_v2() -> None:
    originals = [
        {
            "case_id": "evd-001",
            "mode": "zhoda",
            "action_correct": False,
            "chosen_action": "old",
        }
    ]
    scored = [
        {
            "case_id": "evd-001",
            "mode": "zhoda",
            "action_correct": True,
            "chosen_action": "Keep the index",
            "evaluator_usage": {"requests": 0, "cache_hits": 2, "usd": 0.0},
        }
    ]
    out = annotate_v1_fields(scored, originals)
    assert out[0]["action_correct_v1"] is False
    assert out[0]["action_correct_v2"] is True
    assert out[0]["chosen_action_v1"] == "old"
    assert out[0]["resumed_from_cache"] is True


def test_dropped_ungraded_and_incomplete() -> None:
    scored = [
        {
            "case_id": "a",
            "mode": "zhoda",
            "coverage_status": "ok",
            "grade_status": "graded",
        },
        {
            "case_id": "a",
            "mode": "short_review",
            "coverage_status": "ok",
            "grade_status": "ungraded",
        },
        {
            "case_id": "a",
            "mode": "majority",
            "coverage_status": "ok",
            "grade_status": "graded",
        },
        {
            "case_id": "b",
            "mode": "zhoda",
            "coverage_status": "ok",
            "grade_status": "graded",
        },
        {
            "case_id": "b",
            "mode": "short_review",
            "coverage_status": "failed",
            "grade_status": "graded",
        },
        {
            "case_id": "b",
            "mode": "majority",
            "coverage_status": "ok",
            "grade_status": "graded",
        },
    ]
    dropped = dropped_cases(scored)
    assert [r["case_id"] for r in dropped["dropped_ungraded"]] == ["a"]
    assert [r["case_id"] for r in dropped["dropped_incomplete"]] == ["b"]


def test_primary_requires_three_graded_arms() -> None:
    scored = [
        {
            "case_id": "evd-001",
            "mode": "zhoda",
            "kind": "evidence_required",
            "coverage_status": "ok",
            "grade_status": "graded",
            "action_correct_v2": True,
            "usd": 0.01,
        },
        {
            "case_id": "evd-001",
            "mode": "short_review",
            "kind": "evidence_required",
            "coverage_status": "ok",
            "grade_status": "ungraded",
            "action_correct_v2": None,
            "usd": 0.004,
        },
        {
            "case_id": "evd-001",
            "mode": "majority",
            "kind": "evidence_required",
            "coverage_status": "ok",
            "grade_status": "graded",
            "action_correct_v2": True,
            "usd": 0.002,
        },
    ]
    primary = paired_fully_graded(scored)
    assert primary["n_paired"] == 0
    assert primary["delta"] is None


def test_rescore_report_v2_does_not_mutate_source_and_skips_rule(tmp_path: Path) -> None:
    src_payload = _triple("evd-001")
    src = tmp_path / "report.json"
    src.write_text(json.dumps(src_payload), encoding="utf-8")
    before = src.read_bytes()
    provider = FakeProvider(
        {"picked_id": "Keep the index", "committed": True, "quote": "should be retained"}
    )
    gold_map = load_gold(GOLD_JSONL)
    public_by_id = {c.id: c for c in load_public_cases()}
    out = _run(
        rescore_report_v2(
            src_payload,
            gold_map,
            public_by_id,
            provider=provider,
            model="openai/gpt-4o-mini",
            judge_overlap=("protocol_judge",),
            source_sha256="deadbeef",
        )
    )
    assert src.read_bytes() == before
    assert out["schema"] == SCHEMA_RESCORE_V2
    assert out["grader_version"] == GRADER_VERSION
    assert out["independent_validation"] is False
    assert out["decision_rule"]["verdict"] == "pending_rerun"
    assert out["product_default"] == "debate"
    assert out["n_paired_fully_graded"] == 1
    by_mode = {r["mode"]: r for r in out["results"]}
    assert by_mode["zhoda"]["action_correct_v1"] is False
    assert by_mode["zhoda"]["action_correct_v2"] is True
    assert by_mode["zhoda"]["action_correct_heuristic"] is False
    assert "short_review" not in provider.prompts[0]
    assert "Recommended (majority at cap" not in provider.prompts[0]
    assert "Dissent:" not in provider.prompts[0]
    assert out["primary"]["n_cached_short_review"] == 1
    assert out["primary"]["mean_usd_short_review"] is None
    assert out["primary"]["mean_usd_oxford"] == pytest.approx(0.01)
    assert out["results"][0]["engine_cost_status"] == "exact"
    assert out["results"][1]["engine_cost_status"] == "cached"


def test_ungraded_row_drops_case_from_primary() -> None:
    src_payload = _triple("evd-001")
    provider = FakeProvider({"committed": "false", "picked_id": "Keep the index", "quote": "x"})
    gold_map = load_gold(GOLD_JSONL)
    public_by_id = {c.id: c for c in load_public_cases()}
    out = _run(
        rescore_report_v2(
            src_payload,
            gold_map,
            public_by_id,
            provider=provider,
            model="j",
            judge_overlap=(),
            source_sha256="x",
        )
    )
    assert out["n_paired_fully_graded"] == 0
    assert out["dropped_ungraded"][0]["case_id"] == "evd-001"
    assert out["results"][0]["grade_status"] == "ungraded"
    assert out["results"][0]["action_correct_v1"] is False
    assert out["results"][0]["action_correct_v2"] is None
