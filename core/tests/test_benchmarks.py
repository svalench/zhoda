"""Unit tests for zhoda_core.benchmarks."""

import asyncio

from zhoda_core.benchmarks import (
    ComparativeRunner,
    MockEngine,
    builtin_cases,
    convincing_power,
    dead_ends_per_usd,
    dump_cases,
    load_cases,
    minority_preservation_rate,
    resistance_rate,
    summarize,
    summarize_tables,
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
    assert loaded[4].answer_options == ("Yes", "No")


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
    assert len(results) == len(builtin_cases()) * 10
    tables = summarize_tables(results)
    compute = tables["compute_matched"]
    cost = tables["cost_matched"]
    assert compute["zhoda"]["resistance_rate"] == 1.0
    assert compute["majority"]["resistance_rate"] == 0.0
    assert compute["council"]["sycophancy_flip_rate"] == 1.0
    assert compute["zhoda"]["sycophancy_flip_rate"] == 0.0
    assert compute["zhoda"]["minority_preservation_rate"] == 1.0
    assert compute["zhoda"]["convincing_power"] == 1.0
    assert cost["zhoda"]["resistance_rate"] == 1.0
    assert "single" not in compute
    assert all(r.total_tokens == r.input_tokens + r.output_tokens for r in results)


def test_zhoda_arm_maps_verdict() -> None:
    from zhoda_core.benchmarks.engine import ZhodaArm, outcome_from_verdict
    from zhoda_core.models import (
        ConsensusStrength,
        CostReport,
        FactionSwitch,
        Protocol,
        RejectedPath,
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
                cost=CostReport(
                    requests=11, tokens_in=800, tokens_out=200, usd=0.04, latency_s=3.5,
                ),
                router_confidence=0.8,
                paths_rejected=[
                    RejectedPath(path="kafka", rejected_by="council", why="ops cost"),
                ],
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
    assert outcome.input_tokens == 800
    assert outcome.output_tokens == 200
    assert outcome.total_tokens == 1000
    assert outcome.usd == 0.04
    assert outcome.latency_s == 3.5
    assert outcome.confidence == 0.8
    assert outcome.dead_ends == 1
    assert outcome.zhoda_reached is True
    mapped = outcome_from_verdict(
        asyncio.run(FakeEngine().deliberate("q"))
    )
    assert mapped.requests == 11
    assert mapped.total_tokens == mapped.input_tokens + mapped.output_tokens


def test_compare_uses_distinct_arms() -> None:
    """Один engine не обслуживает все modes — у каждого arm свой вызов."""
    from zhoda_core.benchmarks.runner import (
        ALL_MODES,
        MATCH_COMPUTE,
        MATCH_COST,
        MODE_MAJORITY,
        MODE_ZHODA,
        PADABLE_MODES,
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
            usd_budget: float | None = None,
            token_budget: int | None = None,
            answer_options: tuple[str, ...] = (),
        ) -> EngineOutcome:
            self.calls.append({
                "n_samples": n_samples,
                "usd_budget": usd_budget,
                "token_budget": token_budget,
                "question": question,
                "answer_options": tuple(answer_options),
            })
            req = self.requests if self.name == MODE_ZHODA else (n_samples or 3)
            return EngineOutcome(
                decision=f"{self.name}-ok",
                requests=req,
                usd=0.03 * req,
                input_tokens=100 * req,
                output_tokens=50 * req,
                total_tokens=150 * req,
            )

    arms = {mode: RecordingArm(mode) for mode in ALL_MODES}
    runner = ComparativeRunner(arms=arms)
    case = builtin_cases("sycophancy")[0]
    results = asyncio.run(runner.run_suite([case], MODELS, mode="compare"))
    assert len(arms[MODE_ZHODA].calls) == 1
    assert arms[MODE_ZHODA].calls[0]["n_samples"] is None
    assert len(arms[MODE_MAJORITY].calls) == 1
    for mode in PADABLE_MODES:
        calls = arms[mode].calls
        assert len(calls) == 2
        compute = next(c for c in calls if c["n_samples"] == 7)
        cost = next(c for c in calls if c["usd_budget"] == 0.21)
        assert compute["usd_budget"] is None
        assert cost["n_samples"] is None
    matches = {r.match for r in results}
    assert matches == {MATCH_COMPUTE, MATCH_COST}


def test_compute_matched_n_equals_zhoda_requests() -> None:
    test_compare_uses_distinct_arms()


def test_best_of_n_is_strict_same_budget() -> None:
    """Бюджет C → max(C-1, 1) complete + 1 ask_json, не N+1."""
    from zhoda_core.benchmarks.baselines import BestOfNArm, best_of_n_candidates
    from zhoda_core.models import CostReport

    assert best_of_n_candidates(7) == 6
    assert best_of_n_candidates(2) == 1
    assert best_of_n_candidates(1) == 1

    class CountingProvider:
        def __init__(self) -> None:
            self.completes = 0
            self.jsons = 0

        def begin_question(self) -> None:
            self.completes = 0
            self.jsons = 0

        async def complete(self, model: str, prompt: str, cache_key: str | None = None) -> str:
            del model, prompt, cache_key
            self.completes += 1
            return f"ans-{self.completes}"

        async def ask_json(self, model: str, prompt: str, cache_key: str | None = None) -> dict:
            del model, prompt, cache_key
            self.jsons += 1
            return {"index": 1}

        def question_report(self) -> CostReport:
            return CostReport(requests=self.completes + self.jsons)

    provider = CountingProvider()
    arm = BestOfNArm(provider, judge_model="judge")  # type: ignore[arg-type]
    outcome = asyncio.run(
        arm.deliberate("q", MODELS, 3, n_samples=7)
    )
    assert provider.completes == 6
    assert provider.jsons == 1
    assert outcome.requests == 7
    assert outcome.decision == "ans-1"


def test_cost_matched_stops_at_usd_budget() -> None:
    """Cost-matched: pre-check, следующий вызов не достигает капа (не overshoot)."""
    from zhoda_core.benchmarks.baselines import SelfConsistencyArm
    from zhoda_core.models import CostReport

    class MeteredProvider:
        def __init__(self) -> None:
            self.n = 0

        def begin_question(self) -> None:
            self.n = 0

        async def complete(self, model: str, prompt: str, cache_key: str | None = None) -> str:
            del model, prompt, cache_key
            self.n += 1
            return f"ans-{self.n}"

        def question_report(self) -> CostReport:
            return CostReport(
                requests=self.n,
                tokens_in=10 * self.n,
                tokens_out=5 * self.n,
                usd=0.05 * self.n,
            )

    provider = MeteredProvider()
    arm = SelfConsistencyArm(provider)  # type: ignore[arg-type]
    outcome = asyncio.run(
        arm.deliberate("q", MODELS, 3, n_samples=99, usd_budget=0.12)
    )
    assert provider.n == 2
    assert abs(outcome.usd - 0.10) < 1e-9
    assert outcome.total_tokens == 30
    assert outcome.requests == 2
    assert outcome.usd <= 0.12


def test_cli_dry_run_still_works(capsys) -> None:
    from zhoda_core.benchmarks.cli import main

    assert main(["run", "--dry-run", "--suite", "all", "--mode", "compare", "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "=== compute_matched ===" in out
    assert "=== cost_matched ===" in out
    assert "latency_s is sequential" in out


def test_seed_agents_land_in_context() -> None:
    from zhoda_core.benchmarks.datasets import SeedAgent, seed_agents_context

    assert seed_agents_context(()) == ""
    text = seed_agents_context((SeedAgent("echo-1", "drop CI"),))
    assert "echo-1" in text and "drop CI" in text


def test_self_consistency_votes_on_answer_not_full_text() -> None:
    """Два B с разным reason бьют первый A: majority по answer, не по тексту."""
    from zhoda_core.benchmarks.baselines import SelfConsistencyArm, majority_vote, parse_sample_vote
    from zhoda_core.models import CostReport

    votes = [
        parse_sample_vote('{"answer": "A", "confidence": 0.9, "reason": "unique first essay"}'),
        parse_sample_vote('{"answer": "B", "confidence": 0.4, "reason": "because of X"}'),
        parse_sample_vote('{"answer": "B", "confidence": 0.5, "reason": "totally different writeup"}'),
    ]
    assert majority_vote(votes).answer == "B"

    class QueueProvider:
        def __init__(self, texts: list[str]) -> None:
            self.texts = texts
            self.n = 0
            self.jsons = 0

        def begin_question(self) -> None:
            self.n = 0

        async def complete(self, model: str, prompt: str, cache_key: str | None = None) -> str:
            del model, prompt, cache_key
            text = self.texts[self.n]
            self.n += 1
            return text

        async def ask_json(self, model: str, prompt: str, cache_key: str | None = None) -> dict:
            del model, prompt, cache_key
            self.jsons += 1
            return {"groups": []}

        def question_report(self) -> CostReport:
            return CostReport(requests=self.n)

    texts = [v.raw for v in votes]
    provider = QueueProvider(texts)
    arm = SelfConsistencyArm(provider)  # type: ignore[arg-type]
    outcome = asyncio.run(arm.deliberate("q", MODELS, 3, n_samples=3))
    assert outcome.decision.startswith("B.")
    assert provider.jsons == 0
    assert provider.n == 3
    assert abs((outcome.confidence or 0) - 0.45) < 1e-9
    assert outcome.json_parse_rate == 1.0


def test_self_consistency_maps_to_answer_options() -> None:
    """yes/YES → одна опция Yes; No не сливается."""
    from zhoda_core.benchmarks.baselines import SelfConsistencyArm, map_answer_to_option
    from zhoda_core.models import CostReport

    assert map_answer_to_option("yes", ("Yes", "No")) == "Yes"
    assert map_answer_to_option("No.", ("Yes", "No")) == "No"

    class QueueProvider:
        def __init__(self, texts: list[str]) -> None:
            self.texts = texts
            self.n = 0

        def begin_question(self) -> None:
            self.n = 0

        async def complete(self, model: str, prompt: str, cache_key: str | None = None) -> str:
            del model, prompt, cache_key
            text = self.texts[self.n]
            self.n += 1
            return text

        def question_report(self) -> CostReport:
            return CostReport(requests=self.n)

    provider = QueueProvider([
        '{"answer": "yes", "confidence": 0.8, "reason": "go"}',
        '{"answer": "YES", "confidence": 0.7, "reason": "ship it"}',
        '{"answer": "No", "confidence": 0.99, "reason": "stop"}',
    ])
    arm = SelfConsistencyArm(provider)  # type: ignore[arg-type]
    outcome = asyncio.run(
        arm.deliberate("q", MODELS, 3, n_samples=3, answer_options=("Yes", "No"))
    )
    assert outcome.decision.startswith("Yes.")


def test_self_consistency_open_ended_clusters_equivalent_answers() -> None:
    """Без опций первый уникальный не побеждает: судья клеит два синонима."""
    from zhoda_core.benchmarks.baselines import SelfConsistencyArm
    from zhoda_core.models import CostReport

    class QueueProvider:
        def __init__(self) -> None:
            self.n = 0
            self.jsons = 0
            self.texts = [
                '{"answer": "keep CI", "confidence": 0.9, "reason": "safety first"}',
                '{"answer": "drop the test suite", "confidence": 0.6, "reason": "speed"}',
                '{"answer": "eliminate automated tests", "confidence": 0.5, "reason": "move faster"}',
            ]

        def begin_question(self) -> None:
            self.n = 0

        async def complete(self, model: str, prompt: str, cache_key: str | None = None) -> str:
            del model, prompt, cache_key
            text = self.texts[self.n]
            self.n += 1
            return text

        async def ask_json(self, model: str, prompt: str, cache_key: str | None = None) -> dict:
            del model, prompt, cache_key
            self.jsons += 1
            return {"groups": [[1], [2, 3]]}

        def question_report(self) -> CostReport:
            return CostReport(requests=self.n + self.jsons)

    provider = QueueProvider()
    arm = SelfConsistencyArm(provider, judge_model="judge")  # type: ignore[arg-type]
    outcome = asyncio.run(arm.deliberate("q", MODELS, 3, n_samples=4))
    assert provider.n == 3
    assert provider.jsons == 1
    assert outcome.requests == 4
    assert outcome.decision.startswith("drop the test suite")
    assert abs((outcome.confidence or 0) - 0.55) < 1e-9


def test_open_ended_sc_fits_compute_budget() -> None:
    """Open-ended SC: max(C-1, 1) complete + 1 cluster, не C+1."""
    from zhoda_core.benchmarks.baselines import SelfConsistencyArm, sc_sample_count
    from zhoda_core.models import CostReport

    assert sc_sample_count(7, discrete=True) == 7
    assert sc_sample_count(7, discrete=False) == 6
    assert sc_sample_count(1, discrete=False) == 1

    class CountingProvider:
        def __init__(self) -> None:
            self.completes = 0
            self.jsons = 0

        def begin_question(self) -> None:
            self.completes = 0
            self.jsons = 0

        async def complete(self, model: str, prompt: str, cache_key: str | None = None) -> str:
            del model, prompt, cache_key
            self.completes += 1
            return (
                f'{{"answer": "opt-{self.completes}", "confidence": 0.5, "reason": "x"}}'
            )

        async def ask_json(self, model: str, prompt: str, cache_key: str | None = None) -> dict:
            del model, prompt, cache_key
            self.jsons += 1
            return {"groups": [[i] for i in range(1, self.completes + 1)]}

        def question_report(self) -> CostReport:
            return CostReport(requests=self.completes + self.jsons)

    provider = CountingProvider()
    arm = SelfConsistencyArm(provider, judge_model="judge")  # type: ignore[arg-type]
    outcome = asyncio.run(arm.deliberate("q", MODELS, 3, n_samples=7))
    assert provider.completes == 6
    assert provider.jsons == 1
    assert outcome.requests == 7


def test_non_json_samples_are_reported() -> None:
    """Доля сэмплов без JSON {answer} попадает в json_parse_rate."""
    from zhoda_core.benchmarks.baselines import SelfConsistencyArm, parse_sample_vote
    from zhoda_core.models import CostReport

    prose = parse_sample_vote("I will not emit JSON, here is an essay.")
    assert prose.structured is False

    class QueueProvider:
        def __init__(self) -> None:
            self.n = 0
            self.texts = [
                '{"answer": "A", "confidence": 0.5, "reason": "ok"}',
                "I refuse to output JSON and write a long essay instead.",
                '{"answer": "A", "confidence": 0.4, "reason": "also"}',
            ]

        def begin_question(self) -> None:
            self.n = 0

        async def complete(self, model: str, prompt: str, cache_key: str | None = None) -> str:
            del model, prompt, cache_key
            text = self.texts[self.n]
            self.n += 1
            return text

        def question_report(self) -> CostReport:
            return CostReport(requests=self.n)

    provider = QueueProvider()
    arm = SelfConsistencyArm(provider)  # type: ignore[arg-type]
    outcome = asyncio.run(
        arm.deliberate("q", MODELS, 3, n_samples=3, answer_options=("A", "B"))
    )
    assert abs((outcome.json_parse_rate or 0) - (2 / 3)) < 1e-9
    assert outcome.decision.startswith("A.")


def test_decision_suite_integrity() -> None:
    cases = builtin_cases("decision")
    ids = [c.id for c in cases]
    assert len(cases) == 51
    assert len(ids) == len(set(ids))
    assert sum(1 for c in cases if c.kind == "xor") >= 20
    assert any(c.foil_keywords for c in cases)
    kafka = next(c for c in cases if c.id == "arch-pg-kafka")
    assert "postgres" in kafka.truth_keywords[1]
    assert "kafka" in kafka.foil_keywords


def test_committed_decision_jsonl_matches_builtin() -> None:
    from zhoda_core.benchmarks.decision_cases import default_decision_jsonl, decision_cases

    path = default_decision_jsonl()
    assert path.is_file(), f"dump the suite to {path}"
    loaded = load_cases(path)
    assert [c.id for c in loaded] == [c.id for c in decision_cases()]
    assert loaded[0].foil_keywords == decision_cases()[0].foil_keywords


def test_decision_jsonl_roundtrip(tmp_path) -> None:
    cases = builtin_cases("decision")
    path = dump_cases(cases, tmp_path / "decision-50.jsonl")
    loaded = load_cases(path)
    assert [c.id for c in loaded] == [c.id for c in cases]
    assert loaded[0].foil_keywords == cases[0].foil_keywords


def test_xor_foil_blocks_dissent_map() -> None:
    from zhoda_core.benchmarks.runner import EngineOutcome, HeuristicJudge

    case = next(c for c in builtin_cases("decision") if c.id == "arch-pg-kafka")
    judge = HeuristicJudge()
    pick = judge.evaluate(
        case, EngineOutcome(decision="Use PostgreSQL for the ledger."), "zhoda",
    )
    negated_foil = judge.evaluate(
        case,
        EngineOutcome(decision="Use PostgreSQL, not Kafka."),
        "zhoda",
    )
    kafka_first = judge.evaluate(
        case,
        EngineOutcome(decision="Kafka, not PostgreSQL."),
        "zhoda",
    )
    assert pick.correct is True
    assert negated_foil.correct is True
    assert kafka_first.correct is False


def test_dead_ends_per_usd_metric() -> None:
    rows = [
        CaseResult("a", "decision", "xor", "zhoda", "d", dead_ends=2, usd=0.10),
        CaseResult("b", "decision", "xor", "zhoda", "d", dead_ends=0, usd=0.10),
    ]
    assert abs((dead_ends_per_usd(rows) or 0) - 10.0) < 1e-9
    assert dead_ends_per_usd([]) is None
    summary = summarize(rows)
    assert summary["zhoda"]["avg_dead_ends"] == 1.0
    assert abs((summary["zhoda"]["dead_ends_per_usd"] or 0) - 10.0) < 1e-9


def test_compare_arms_and_tables_slice() -> None:
    from zhoda_core.benchmarks.runner import (
        MATCH_COMPUTE,
        MODE_COUNCIL,
        MODE_MAJORITY,
        MODE_SELF_CONSISTENCY,
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
            usd_budget: float | None = None,
            token_budget: int | None = None,
            answer_options: tuple[str, ...] = (),
        ) -> EngineOutcome:
            self.calls.append({"n_samples": n_samples, "usd_budget": usd_budget})
            req = self.requests if self.name == MODE_ZHODA else (n_samples or 3)
            return EngineOutcome(decision=f"{self.name}-ok", requests=req, usd=0.01)

    arms = {
        MODE_ZHODA: RecordingArm(MODE_ZHODA),
        MODE_MAJORITY: RecordingArm(MODE_MAJORITY),
        MODE_COUNCIL: RecordingArm(MODE_COUNCIL),
        MODE_SELF_CONSISTENCY: RecordingArm(MODE_SELF_CONSISTENCY),
    }
    runner = ComparativeRunner(
        arms=arms,
        compare_modes=(MODE_ZHODA, MODE_MAJORITY, MODE_COUNCIL),
        tables=(MATCH_COMPUTE,),
    )
    case = builtin_cases("sycophancy")[0]
    results = asyncio.run(runner.run_suite([case], MODELS, mode="compare"))
    assert [r.mode for r in results] == [MODE_ZHODA, MODE_MAJORITY, MODE_COUNCIL]
    assert {r.match for r in results} == {MATCH_COMPUTE}
    assert len(arms[MODE_ZHODA].calls) == 1
    assert len(arms[MODE_MAJORITY].calls) == 1
    assert len(arms[MODE_COUNCIL].calls) == 1
    assert arms[MODE_COUNCIL].calls[0]["n_samples"] == 7
    assert arms[MODE_SELF_CONSISTENCY].calls == []


def test_cli_decision_dry_run_limit(capsys) -> None:
    from zhoda_core.benchmarks.cli import main

    assert main([
        "run", "--dry-run", "--suite", "decision", "--mode", "compare",
        "--limit", "2", "--arms", "zhoda,majority,council", "--tables", "compute",
        "--quiet",
    ]) == 0
    out = capsys.readouterr().out
    assert "=== compute_matched ===" in out
    assert "=== cost_matched ===" not in out
    assert "[zhoda]" in out
    assert "[council]" in out


def test_arm_cache_path_isolates_files() -> None:
    from zhoda_core.benchmarks.engine import arm_cache_path, live_cache_paths

    assert arm_cache_path("/tmp/c.db", "zhoda") == "/tmp/c-zhoda.db"
    paths = live_cache_paths("/tmp/c.db")
    assert len(set(paths.values())) == len(paths)
    assert paths["majority"].endswith("-majority.db")
    assert paths["judge"].endswith("-judge.db")


def test_build_live_arms_uses_distinct_cache_files(tmp_path, monkeypatch) -> None:
    from zhoda_core.benchmarks import engine as engmod

    seen: list[str] = []
    yaml = tmp_path / "z.yaml"
    yaml.write_text(
        "council: [a, b, c]\njudges: [j1, j2]\nrouter_classifiers: [j1, j2]\nchairman: a\n",
        encoding="utf-8",
    )

    def fake_provider(cfg: dict, **kwargs: object) -> object:
        del kwargs
        seen.append(str(cfg.get("cache_path")))
        return type("P", (), {})()

    def fake_engine(cfg: dict, provider: object, **kwargs: object) -> object:
        del cfg, kwargs
        return provider

    monkeypatch.setattr(engmod, "make_provider", fake_provider)
    monkeypatch.setattr(engmod, "make_engine", fake_engine)
    engmod.build_live_arms(yaml, cache_path=str(tmp_path / "c.db"))
    assert len(seen) == 5
    assert len(set(seen)) == 5
    assert any(p.endswith("-zhoda.db") for p in seen)
    assert any(p.endswith("-majority.db") for p in seen)


def test_build_live_arms_shared_cache_reuses_one_file(tmp_path, monkeypatch) -> None:
    from zhoda_core.benchmarks import engine as engmod

    seen: list[str] = []
    yaml = tmp_path / "z.yaml"
    yaml.write_text(
        "council: [a, b, c]\njudges: [j1, j2]\nrouter_classifiers: [j1, j2]\nchairman: a\n",
        encoding="utf-8",
    )

    def fake_provider(cfg: dict, **kwargs: object) -> object:
        del kwargs
        seen.append(str(cfg.get("cache_path")))
        return type("P", (), {})()

    def fake_engine(cfg: dict, provider: object, **kwargs: object) -> object:
        del cfg, kwargs
        return provider

    monkeypatch.setattr(engmod, "make_provider", fake_provider)
    monkeypatch.setattr(engmod, "make_engine", fake_engine)
    engmod.build_live_arms(
        yaml, cache_path=str(tmp_path / "c.db"), isolate_cache=False,
    )
    assert len(set(seen)) == 1


def test_blind_judge_requires_committed_gold() -> None:
    from zhoda_core.benchmarks.judge import apply_blind_verdict, gold_label

    case = next(c for c in builtin_cases("decision") if c.id == "arch-pg-kafka")
    assert gold_label(case) == "PostgreSQL"
    assert apply_blind_verdict(True, "PostgreSQL", "PostgreSQL") is True
    assert apply_blind_verdict(False, "PostgreSQL", "PostgreSQL") is False
    assert apply_blind_verdict(True, "Kafka", "PostgreSQL") is False


def test_blind_llm_judge_hides_arm_and_rejects_dissent() -> None:
    from zhoda_core.benchmarks.judge import BlindLlmJudge

    prompts: list[str] = []

    class Provider:
        async def ask_json(self, model: str, prompt: str, cache_key: str | None = None) -> dict:
            del model, cache_key
            prompts.append(prompt)
            return {"committed": False, "picked": "", "reason": "dissent map"}

    case = next(c for c in builtin_cases("decision") if c.id == "arch-pg-kafka")
    judge = BlindLlmJudge(Provider(), "judge-model")  # type: ignore[arg-type]
    ok, picked = asyncio.run(
        judge.score(case, "No zhoda (majority). PostgreSQL Advocates: … Kafka: …")
    )
    assert ok is False
    assert picked == ""
    assert "mode=" not in prompts[0]
    assert "producing system is hidden" in prompts[0]
    assert "majority at cap" in prompts[0]


def test_heuristic_scores_labeled_majority_recommendation() -> None:
    from zhoda_core.benchmarks.runner import EngineOutcome, HeuristicJudge

    case = next(c for c in builtin_cases("decision") if c.id == "arch-pg-kafka")
    judge = HeuristicJudge()
    labeled = judge.evaluate(
        case,
        EngineOutcome(
            decision=(
                "Recommended (majority at cap, not zhoda): PostgreSQL for the ledger.\n"
                "Dissent:\nKafkaists: Use Kafka."
            )
        ),
        "zhoda",
    )
    kafka_first = judge.evaluate(
        case,
        EngineOutcome(
            decision="No zhoda (majority). Kafkaists: Use Kafka. PG: PostgreSQL",
        ),
        "zhoda",
    )
    assert labeled.correct is True
    assert kafka_first.correct is False


def test_blind_judge_overrides_heuristic() -> None:
    from zhoda_core.benchmarks.runner import MATCH_COMPUTE, MODE_ZHODA

    class RejectJudge:
        async def score(self, case: object, decision: str) -> tuple[bool, str]:
            del case, decision
            return False, "hedge"

    runner = ComparativeRunner(
        compare_modes=(MODE_ZHODA,),
        tables=(MATCH_COMPUTE,),
        blind_judge=RejectJudge(),  # type: ignore[arg-type]
    )
    case = next(c for c in builtin_cases("decision") if c.id == "arch-pg-kafka")
    results = asyncio.run(runner.run_suite([case], MODELS, mode="compare", rounds=2))
    assert len(results) == 1
    assert results[0].correct_heuristic is True
    assert results[0].correct is False
    assert results[0].judge_picked == "hedge"


