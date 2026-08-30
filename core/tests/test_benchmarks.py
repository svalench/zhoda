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
    assert len(results) == len(builtin_cases()) * 5
    summary = summarize(results)
    assert summary["zhoda"]["resistance_rate"] == 1.0
    assert summary["majority"]["resistance_rate"] == 0.0
    assert summary["council"]["sycophancy_flip_rate"] == 1.0
    assert summary["zhoda"]["sycophancy_flip_rate"] == 0.0
    assert summary["zhoda"]["minority_preservation_rate"] == 1.0
    assert summary["zhoda"]["convincing_power"] == 1.0
    assert "single" not in summary


def test_zhoda_arm_maps_verdict() -> None:
    from zhoda_core.benchmarks.engine import ZhodaArm, outcome_from_verdict
    from zhoda_core.models import (
        ConsensusStrength,
        CostReport,
        FactionSwitch,
        Protocol,
        Verdict,
    )

    class FakeEngine:
        async def deliberate(self, question: str, **kwargs: object) -> Verdict:
            del question, kwargs
            return Verdict(
                decision="use postgres",
                zhoda_reached=True,
                consensus_strength=ConsensusStrength.MAJORITY,
                protocol=Protocol.DEBATE,
                minority_report="kafka",
                switches=[
                    FactionSwitch(
                        model="m1",
                        from_faction="A",
                        to_faction="B",
                        convinced_by="closed write-scaling",
                        objection_id="1",
                    )
                ],
                rounds_taken=2,
                cost=CostReport(requests=11),
                router_confidence=0.8,
            )

    arm = ZhodaArm(FakeEngine(), protocol=Protocol.DEBATE)
    outcome = asyncio.run(
        arm.deliberate("postgres or kafka?", MODELS, 3)
    )
    assert outcome.decision == "use postgres"
    assert outcome.minority_report == "kafka"
    assert outcome.switches == 1
    assert outcome.rounds_taken == 2
    assert outcome.requests == 11
    assert outcome.confidence == 0.8
    mapped = outcome_from_verdict(
        asyncio.run(FakeEngine().deliberate("q"))
    )
    assert mapped.requests == 11


def test_compare_uses_distinct_arms() -> None:
    """Один engine не обслуживает все modes — у каждого arm свой вызов."""
    from zhoda_core.benchmarks.runner import (
        ALL_MODES,
        MODE_ZHODA,
        EngineOutcome,
    )

    class RecordingArm:
        def __init__(self, name: str, requests: int = 7) -> None:
            self.name = name
            self.calls: list[dict] = []
            self.requests = requests

        async def deliberate(
            self,
            question: str,
            models: list[str],
            rounds: int,
            seed_agents: tuple = (),
            *,
            n_samples: int | None = None,
        ) -> EngineOutcome:
            self.calls.append({"n_samples": n_samples, "question": question})
            req = self.requests if self.name == MODE_ZHODA else (n_samples or 0)
            return EngineOutcome(decision=f"{self.name}-ok", requests=req)

    arms = {mode: RecordingArm(mode) for mode in ALL_MODES}
    runner = ComparativeRunner(arms=arms)
    case = builtin_cases("sycophancy")[0]
    asyncio.run(runner.run_suite([case], MODELS, mode="compare"))
    assert len(arms[MODE_ZHODA].calls) == 1
    assert arms[MODE_ZHODA].calls[0]["n_samples"] is None
    for mode in ALL_MODES:
        if mode == MODE_ZHODA:
            continue
        assert len(arms[mode].calls) == 1
        assert arms[mode].calls[0]["n_samples"] == 7


def test_compute_matched_n_equals_zhoda_requests() -> None:
    test_compare_uses_distinct_arms()


def test_cli_dry_run_still_works() -> None:
    from zhoda_core.benchmarks.cli import main

    assert main(["run", "--dry-run", "--suite", "all", "--mode", "compare"]) == 0


def test_seed_agents_land_in_context() -> None:
    from zhoda_core.benchmarks.datasets import SeedAgent, seed_agents_context

    assert seed_agents_context(()) == ""
    text = seed_agents_context((SeedAgent("echo-1", "drop CI"),))
    assert "echo-1" in text and "drop CI" in text
