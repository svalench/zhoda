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
    paired_fully_graded,
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
    sr = dict(zhoda, mode="short_review", usd=0.004, requests=0)
    maj = dict(zhoda, mode="majority", usd=0.002, decision="Keep the index", action_correct=True)
    if failed_maj:
        maj["coverage_status"] = "failed"
        maj["decision"] = ""
    return {"schema": "zhoda.eval.live_g.v1", "n_cases": 1, "results": [zhoda, sr, maj]}


def test_live_g_v1_report_hash_frozen() -> None:
    import hashlib

    from zhoda_core.eval.pilot_rescore import SOURCE_REPORT_SHA256

    report = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "live-runs"
        / "2026-09-06-g-pilot"
        / "report.json"
    )
    digest = hashlib.sha256(report.read_bytes()).hexdigest()
    assert digest == SOURCE_REPORT_SHA256


def test_resumed_from_cache_flag() -> None:
    assert resumed_from_cache({"requests": 0, "cache_hits": 3}) is True
    assert resumed_from_cache({"requests": 1, "cache_hits": 3}) is False
    assert resumed_from_cache({"requests": 0, "cache_hits": 0}) is False


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
    assert "Recommended (majority at cap" not in provider.prompts[0].split("System answer")[0]


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
