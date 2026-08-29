"""Unit tests for zhoda_core.benchmarks."""

import asyncio

from zhoda_core.benchmarks import (
    ComparativeRunner,
    MockEngine,
    builtin_cases,
    convincing_power,
    dump_cases,
    load_cases,
    minority_preservation_rate,
    resistance_rate,
    summarize,
    sycophancy_flip_rate,
)
from zhoda_core.benchmarks.runner import CaseResult

MODELS = ["m1", "m2", "m3"]


def test_builtin_datasets_integrity():
    cases = builtin_cases()
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids))
    assert any(c.suite == "sycophancy" for c in cases)
    assert any(c.suite == "minority" for c in cases)
    bandwagon = [c for c in cases if c.kind == "bandwagon"]
    assert all(c.seed_agents for c in bandwagon)


def test_dataset_jsonl_roundtrip(tmp_path):
    cases = builtin_cases()
    path = dump_cases(cases, tmp_path / "cases.jsonl")
    loaded = load_cases(path)
    assert [c.id for c in loaded] == [c.id for c in cases]
    assert loaded[4].seed_agents == cases[4].seed_agents


def test_metrics_on_synthetic_results():
    results = [
        CaseResult("c1", "sycophancy", "biased_premise", "zhoda", "d", resisted_premise=True, correct=True, confidence=0.9),
        CaseResult("c2", "sycophancy", "biased_premise", "zhoda", "d", resisted_premise=False, correct=False, confidence=0.7),
        CaseResult("c3", "sycophancy", "bandwagon", "zhoda", "d", flipped_to_majority=True),
        CaseResult("c4", "minority", "true_minority", "zhoda", "d", minority_preserved=True, convinced_switches=2),
    ]
    assert resistance_rate(results) == 0.5
    assert sycophancy_flip_rate(results) == 1.0
    assert minority_preservation_rate(results) == 1.0
    assert convincing_power(results) == 1.0
    summary = summarize(results)
    assert summary["zhoda"]["n_cases"] == 4.0


def test_metrics_empty_subsets():
    assert resistance_rate([]) is None
    assert sycophancy_flip_rate([]) is None
    assert minority_preservation_rate([]) is None
    assert convincing_power([]) is None


def test_mock_engine_profiles():
    case = builtin_cases("sycophancy")[0]
    honest = asyncio.run(MockEngine("honest").run_case(case, MODELS, 3))
    conformist = asyncio.run(MockEngine("conformist").run_case(case, MODELS, 3))
    assert "Rejected premise" in honest.decision
    assert conformist.decision.startswith("Yes")


def test_compare_run_dry_pipeline():
    runner = ComparativeRunner()
    results = asyncio.run(
        runner.run_suite(builtin_cases(), MODELS, mode="compare", rounds=3)
    )
    assert len(results) == len(builtin_cases()) * 3
    summary = summarize(results)
    assert summary["zhoda"]["resistance_rate"] == 1.0
    assert summary["single"]["resistance_rate"] == 0.0
    assert summary["single"]["sycophancy_flip_rate"] == 1.0
    assert summary["zhoda"]["sycophancy_flip_rate"] == 0.0
    assert summary["zhoda"]["minority_preservation_rate"] == 1.0
    assert summary["zhoda"]["convincing_power"] == 1.0
