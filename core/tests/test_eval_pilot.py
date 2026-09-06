"""Pilot holdout: public≠gold, 36 cases, dry-run without .env."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from zhoda_core.benchmarks.datasets import builtin_cases
from zhoda_core.eval.cases import PUBLIC_FORBIDDEN, SPECS, TASK_CLASSES
from zhoda_core.eval.pilot import (
    GOLD_JSONL,
    PILOT_STATUS,
    PUBLIC_JSONL,
    dump_public,
    load_public_cases,
    public_to_benchmark,
    validate_public,
)


def test_pilot_size_and_classes() -> None:
    assert 30 <= len(SPECS) <= 50
    assert {s.task_class for s in SPECS} == set(TASK_CLASSES)
    assert len({s.id for s in SPECS}) == len(SPECS)


def test_pilot_ids_disjoint_from_development_51() -> None:
    dev = {c.id for c in builtin_cases("decision")}
    assert len(dev) == 51
    overlap = dev.intersection({s.id for s in SPECS})
    assert not overlap


def test_public_jsonl_has_no_gold_keys() -> None:
    dump_public(PUBLIC_JSONL)
    for line in PUBLIC_JSONL.read_text(encoding="utf-8").splitlines():
        raw = json.loads(line)
        assert not PUBLIC_FORBIDDEN.intersection(raw)
        assert "content_hash" in raw["source_bundle"]
    assert validate_public() == []


def test_gold_sidecar_matches_public_ids() -> None:
    from zhoda_core.eval.gold import dump_gold, load_gold

    dump_public(PUBLIC_JSONL)
    dump_gold(GOLD_JSONL)
    gold = load_gold(GOLD_JSONL)
    pub = {c.id for c in load_public_cases()}
    assert set(gold) == pub
    assert all(g.label_status == "provisional" for g in gold.values())
    assert all("not-independent" in g.annotator for g in gold.values())


def test_pilot_module_source_does_not_import_gold() -> None:
    src = Path(__file__).resolve().parents[1] / "src" / "zhoda_core" / "eval" / "pilot.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
    assert not any("gold" in name.split(".")[-1] for name in imported)
    text = src.read_text(encoding="utf-8")
    assert "load_gold" not in text
    assert "expected_action" not in text


def test_runner_case_has_empty_ground_truth() -> None:
    dump_public(PUBLIC_JSONL)
    pub = load_public_cases()[0]
    case = public_to_benchmark(pub)
    assert case.ground_truth == ""
    assert case.suite == "pilot"
    assert case.context == pub.source_bundle["text"]
    assert case.context


def test_runner_forwards_source_context() -> None:
    import asyncio

    from zhoda_core.benchmarks.runner import (
        MATCH_REQUEST,
        MODE_ZHODA,
        ComparativeRunner,
        EngineOutcome,
    )

    seen: dict[str, str] = {}

    class Arm:
        async def deliberate(self, question: str, **kwargs: object) -> EngineOutcome:
            del question
            seen["context"] = str(kwargs.get("context") or "")
            return EngineOutcome(decision="ok")

    dump_public(PUBLIC_JSONL)
    case = public_to_benchmark(load_public_cases()[0])
    runner = ComparativeRunner(
        arms={MODE_ZHODA: Arm()},  # type: ignore[dict-item]
        compare_modes=(MODE_ZHODA,),
        tables=(MATCH_REQUEST,),
    )
    asyncio.run(runner.run_suite([case], ["m1", "m2", "m3"], mode="compare", rounds=1))
    assert seen["context"] == case.context
    assert "Seq Scan" in seen["context"]


def test_compare_pilot_arms_emits_short_review() -> None:
    import asyncio

    from zhoda_core.benchmarks.datasets import builtin_cases
    from zhoda_core.benchmarks.runner import (
        MATCH_REQUEST,
        PILOT_ARMS,
        ComparativeRunner,
        EngineOutcome,
    )

    calls: list[str] = []

    class Arm:
        def __init__(self, name: str) -> None:
            self.name = name

        async def deliberate(self, **kwargs: object) -> EngineOutcome:
            del kwargs
            calls.append(self.name)
            return EngineOutcome(decision=self.name, requests=3, usd=0.01)

    runner = ComparativeRunner(
        arms={m: Arm(m) for m in PILOT_ARMS},  # type: ignore[dict-item]
        compare_modes=PILOT_ARMS,
        tables=(MATCH_REQUEST,),
    )
    case = builtin_cases("sycophancy")[0]
    results = asyncio.run(runner.run_suite([case], ["m1", "m2", "m3"], mode="compare", rounds=1))
    assert set(calls) == set(PILOT_ARMS)
    assert {r.mode for r in results} == set(PILOT_ARMS)


def test_minority_cases_have_injected_positions() -> None:
    dump_public(PUBLIC_JSONL)
    minority = [c for c in load_public_cases() if c.task_class == "correct_minority"]
    assert minority
    for case in minority:
        assert case.seed_agents
        assert case.seed_agents[0].name == "minority"


def test_eval_status_and_dry_run_without_env(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    from zhoda_core.eval.__main__ import main

    dump_public(PUBLIC_JSONL)
    assert main(["status"]) == 0
    out = tmp_path / "dry.json"
    assert main(["dry-run", "--out", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["status"] == PILOT_STATUS
    assert report["live"] is False
    assert report["independent_validation"] is False
    assert report["n_cases"] == len(SPECS)
    assert "short_review" in report["arms"]
    assert "council" not in report["arms"]


def test_cli_pilot_suite_defaults_to_pilot_arms(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    dump_public(PUBLIC_JSONL)
    from zhoda_core.benchmarks.cli import main

    out = tmp_path / "pilot.json"
    assert main([
        "run", "--dry-run", "--suite", "pilot", "--mode", "compare",
        "--quiet", "--out", str(out),
    ]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["arms"] == ["zhoda", "short_review", "majority"]
    assert report["dataset_split"] == "pilot_holdout"
    assert report["independent_validation"] is False
    assert report["n_cases"] == len(SPECS)
    assert report["results"]
    capsys.readouterr()


def test_build_live_arms_pilot_skips_council(tmp_path, monkeypatch) -> None:
    from zhoda_core.benchmarks import engine as engmod
    from zhoda_core.benchmarks.runner import PILOT_ARMS

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
    arms = engmod.build_live_arms(
        yaml, cache_path=str(tmp_path / "c.db"), modes=PILOT_ARMS,
    )
    assert set(arms) == set(PILOT_ARMS)
    assert len(seen) == 3
    assert any(p.endswith("-short_review.db") for p in seen)
    assert not any("council" in p or "self_consistency" in p or "best_of_n" in p for p in seen)


def test_frozen_manifest_hashes_content(tmp_path) -> None:
    from zhoda_core.eval.pilot import FROZEN_MANIFEST, write_frozen_manifest

    before = FROZEN_MANIFEST.read_text(encoding="utf-8")
    target = tmp_path / "frozen-manifest.json"
    man = write_frozen_manifest(target)
    assert FROZEN_MANIFEST.read_text(encoding="utf-8") == before
    assert target.is_file()
    assert man["status"] == PILOT_STATUS
    assert man["live"] is False
    assert man["significance_promised"] is False
    assert man["public_hash"]
    assert man["gold_hash"]
    assert man["prompt_hash"]
    assert man["n_cases"] == 36
    assert man["spend_cap_usd"] == 8.0
    committed = json.loads(before)
    assert man["public_hash"] == committed["public_hash"]
    assert man["gold_hash"] == committed["gold_hash"]
