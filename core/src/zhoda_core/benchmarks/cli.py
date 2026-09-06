"""CLI for the Zhoda benchmark suite.

Usage:
    python -m zhoda_core.benchmarks run --suite sycophancy \
        --models "a/b:free,c/d:free" --rounds 3 --mode compare --dry-run
    python -m zhoda_core.benchmarks export-reputation --output weights.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

from .datasets import builtin_cases, load_cases
from .metrics import summarize_tables
from .runner import (
    ALL_MODES,
    MATCH_COMPUTE,
    MATCH_COST,
    MODE_ZHODA,
    PADABLE_MODES,
    CaseResult,
    ComparativeRunner,
    DeliberationEngine,
    results_to_dicts,
)

_MODE_CHOICES = ("compare", *ALL_MODES)
_SUITE_CHOICES = ("sycophancy", "minority", "decision", "all")


def _parse_csv(value: str | None, allowed: Sequence[str]) -> Tuple[str, ...]:
    if not value:
        return tuple(allowed)
    items = tuple(part.strip() for part in value.split(",") if part.strip())
    bad = [item for item in items if item not in allowed]
    if bad:
        raise argparse.ArgumentTypeError(
            f"unknown values {bad}; allowed: {', '.join(allowed)}"
        )
    return items


def _build_arms(
    config: str,
    clarify_mode: str,
    *,
    rounds_cap: int | None,
    cache_path: str | None,
    transcripts_dir: str | None,
    isolate_cache: bool,
    council: list[str] | None = None,
    replicate_id: int = 0,
    cache_mode: str = "fresh",
    spies: dict[str, Any] | None = None,
) -> dict[str, DeliberationEngine]:
    """Реальный engine; исключения наружу — CLI печатает и выходит 2."""
    from .engine import build_live_arms

    return build_live_arms(
        config,
        clarify_mode=clarify_mode,
        rounds_cap=rounds_cap,
        cache_path=cache_path,
        transcripts_dir=transcripts_dir,
        isolate_cache=isolate_cache,
        council=council,
        replicate_id=replicate_id,
        cache_mode=cache_mode,
        spies=spies,
        expected_models=council,
    )


def _on_result(result: CaseResult) -> None:
    zhoda = "Y" if result.zhoda_reached else "N"
    heur = ""
    if (
        result.correct_heuristic is not None
        and result.correct != result.correct_heuristic
    ):
        heur = f"\theur={result.correct_heuristic}"
    print(
        f"{result.case_id}\t{result.mode}\t{result.match}\t"
        f"ok={result.correct}\tzhoda={zhoda}\tdead={result.dead_ends}\t"
        f"req={result.requests}\tusd={result.usd:.4f}{heur}",
        flush=True,
    )


def _cmd_run(args: argparse.Namespace) -> int:
    cases = load_cases(args.dataset) if args.dataset else builtin_cases(args.suite)
    if args.kind:
        cases = [c for c in cases if c.kind == args.kind]
    if args.offset:
        cases = cases[args.offset :]
    if args.limit is not None:
        cases = cases[: args.limit]
    if not cases:
        print("no benchmark cases found", file=sys.stderr)
        return 2

    models_arg: List[str] | None = (
        [m.strip() for m in args.models.split(",") if m.strip()]
        if args.models
        else None
    )

    try:
        compare_modes = _parse_csv(args.arms, ALL_MODES)
    except argparse.ArgumentTypeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.mode == "compare" and any(m in PADABLE_MODES for m in compare_modes):
        if MODE_ZHODA not in compare_modes:
            compare_modes = (MODE_ZHODA, *compare_modes)

    if args.tables == "compute" or args.tables == "request":
        tables: Tuple[str, ...] = (MATCH_COMPUTE,)
    elif args.tables == "cost":
        tables = (MATCH_COST,)
    else:
        tables = (MATCH_COMPUTE, MATCH_COST)

    yaml_cfg = None
    cfg_path = Path(args.config)
    if cfg_path.exists():
        try:
            from zhoda_core.config import load_council_config

            yaml_cfg = load_council_config(args.config)
        except Exception as exc:  # noqa: BLE001
            if not args.dry_run:
                print(f"zhoda-core engine not available: {exc}", file=sys.stderr)
                return 2

    from .spec import InapplicableOverride, resolve_run_spec

    try:
        spec = resolve_run_spec(
            cases=cases,
            yaml_cfg=yaml_cfg,
            models_override=models_arg,
            rounds_override=args.rounds,
            budget_override=args.budget,
            cache_path=args.cache_path,
            isolate_cache=not args.shared_cache,
            cache_mode=args.cache_mode,
            replicate_id=args.replicate,
            clarify_mode=args.clarify,
            dry_run=args.dry_run,
            judge_model_override=args.judge_model,
        )
    except InapplicableOverride as exc:
        print(str(exc), file=sys.stderr)
        return 2

    models: List[str] = list(spec.roster.council)
    spies: dict[str, Any] = {}
    if not args.dry_run:
        from .spy import CallSpy

        spies = {
            mode: CallSpy(
                spec.roster.allowed_models(),
                expected_max_tokens=spec.max_tokens,
            )
            for mode in compare_modes
        }

    arms = None
    if not args.dry_run:
        try:
            arms = _build_arms(
                args.config,
                args.clarify,
                rounds_cap=spec.rounds,
                cache_path=args.cache_path,
                transcripts_dir=args.transcripts_dir,
                isolate_cache=not args.shared_cache,
                council=models,
                replicate_id=spec.replicate_id,
                cache_mode=spec.cache_mode,
                spies=spies,
            )
        except Exception as exc:  # noqa: BLE001 — CLI boundary: YAML/key/config
            print(
                f"zhoda-core engine not available: {exc}\n"
                "re-run with --dry-run to validate the pipeline on mock profiles",
                file=sys.stderr,
            )
            return 2

    blind_judge = None
    if args.judge == "llm" and not args.dry_run:
        from zhoda_core.config import load_council_config, make_provider

        from .engine import arm_cache_path
        from .judge import BlindLlmJudge
        from .spy import CallSpy, SpyingProvider

        cfg = dict(load_council_config(args.config))
        base = args.cache_path or cfg.get("cache_path") or ".zhoda-cache.db"
        cfg["cache_path"] = arm_cache_path(
            str(base), "judge", replicate_id=spec.replicate_id, cache_mode=spec.cache_mode,
        )
        judge_model = spec.roster.judge_model
        if not judge_model:
            print("no judge model: pass --judge-model or configure judges", file=sys.stderr)
            return 2
        judge_spy = CallSpy(spec.roster.allowed_models())
        spies["judge"] = judge_spy
        blind_judge = BlindLlmJudge(
            SpyingProvider(make_provider(cfg), judge_spy, role="evaluator"),
            judge_model,
            overlap_roles=spec.roster.judge_overlap,
        )

    checkpoint = None
    if args.checkpoint:
        from .checkpoint import CheckpointStore

        checkpoint = CheckpointStore(Path(args.checkpoint))

    runner = ComparativeRunner(
        arms=arms,
        compare_modes=compare_modes,
        tables=tables,
        on_result=None if args.quiet else _on_result,
        blind_judge=blind_judge,
        spec_hash=spec.spec_hash,
        replicate_id=spec.replicate_id,
        checkpoint=checkpoint,
        n_council=len(spec.roster.council),
    )
    results = asyncio.run(
        runner.run_suite(
            cases,
            models,
            mode=args.mode,
            rounds=spec.rounds,
            n_samples=args.n_samples,
        )
    )
    for spy in spies.values():
        spy.verify()
    tables_out = summarize_tables(results)

    report = {
        "suite": args.suite,
        "mode": args.mode,
        "rounds": spec.rounds,
        "models": models,
        "dry_run": args.dry_run,
        "judge": args.judge,
        "judge_model": spec.roster.judge_model,
        "judge_overlap": list(spec.roster.judge_overlap),
        "shared_cache": args.shared_cache,
        "cache_mode": spec.cache_mode,
        "replicate_id": spec.replicate_id,
        "arms": list(compare_modes),
        "tables_requested": list(tables),
        "n_cases": len(cases),
        "case_ids": [c.id for c in cases],
        "dataset_split": spec.dataset_split,
        "manifest": spec.to_manifest(),
        "tables": tables_out,
        "summary": tables_out.get("request_matched") or tables_out.get("cost_matched"),
        "results": results_to_dicts(results),
        "independent_validation": False,
    }

    for label, summary in tables_out.items():
        if not summary:
            continue
        print(f"=== {label} ===")
        if label == "cost_matched":
            print("(compare usd/tokens; latency_s is sequential wall-clock, not parallel)")
        for mode, metrics in summary.items():
            rendered = {
                k: (round(v, 3) if isinstance(v, float) else v)
                for k, v in metrics.items()
                if v is not None
            }
            print(f"[{mode}] {rendered}")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"report written to {out}")
    return 0


def _cmd_rescore(args: argparse.Namespace) -> int:
    """Пересчитать correct слепым LLM по уже сохранённым decision."""
    from dataclasses import fields

    from zhoda_core.config import load_council_config, make_provider

    from .engine import arm_cache_path
    from .judge import BlindLlmJudge, GradeStatus

    path = Path(args.report)
    report = json.loads(path.read_text(encoding="utf-8"))
    cases = {c.id: c for c in builtin_cases("decision")}
    missing = [row["case_id"] for row in report["results"] if row["case_id"] not in cases]
    if missing:
        print(f"unknown case ids: {sorted(set(missing))}", file=sys.stderr)
        return 2
    try:
        cfg = dict(load_council_config(args.config))
        base = args.cache_path or cfg.get("cache_path") or ".zhoda-cache.db"
        cfg["cache_path"] = arm_cache_path(str(base), "judge")
        chairman = str(cfg.get("chairman") or cfg["council"][0])
        judges = cfg.get("judges") or []
        judge_model = str(judges[0] if judges else chairman)
        overlap = []
        if judge_model == chairman:
            overlap.append("chairman")
        judge = BlindLlmJudge(make_provider(cfg), judge_model, overlap_roles=overlap)
    except Exception as exc:  # noqa: BLE001
        print(f"zhoda-core engine not available: {exc}", file=sys.stderr)
        return 2

    async def _run() -> None:
        for row in report["results"]:
            if row.get("correct_heuristic") is None:
                row["correct_heuristic"] = row.get("correct")
            case = cases[str(row["case_id"])]
            grade = await judge.score(case, str(row["decision"]))
            row["grade_status"] = str(grade.status)
            row["grade_error"] = grade.error
            row["judge_picked"] = grade.picked_id
            if grade.status == GradeStatus.GRADED:
                row["correct"] = grade.correct
            else:
                row["correct"] = None
            print(
                f"{row['case_id']}\t{row['mode']}\t"
                f"heur={row['correct_heuristic']}\t"
                f"llm={row['correct']}\tstatus={grade.status}\tpick={grade.picked_id}",
                flush=True,
            )

    asyncio.run(_run())
    allowed = {f.name for f in fields(CaseResult)}
    results = [
        CaseResult(**{k: v for k, v in row.items() if k in allowed})
        for row in report["results"]
    ]
    tables_out = summarize_tables(results)
    report["tables"] = tables_out
    report["summary"] = tables_out.get("request_matched") or tables_out.get("cost_matched")
    report["judge"] = "llm"
    out = Path(args.out) if args.out else path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"rescored report written to {out}")
    for label, summary in tables_out.items():
        if not summary:
            continue
        print(f"=== {label} ===")
        for mode, metrics in summary.items():
            rendered = {
                k: (round(v, 3) if isinstance(v, float) else v)
                for k, v in metrics.items()
                if v is not None
            }
            print(f"[{mode}] {rendered}")
    return 0


def _cmd_export_reputation(args: argparse.Namespace) -> int:
    from zhoda_core.reputation import ReputationStorage

    storage = ReputationStorage(args.path)
    matrix = storage.load()
    payload = json.dumps(matrix.to_dict(), indent=2, sort_keys=True)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload, encoding="utf-8")
        print(f"reputation exported to {out}")
    else:
        print(payload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zhoda-bench", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run a benchmark suite")
    run.add_argument("--suite", choices=list(_SUITE_CHOICES), default="all")
    run.add_argument("--mode", choices=list(_MODE_CHOICES), default="compare")
    run.add_argument("--models", default=None, help="comma-separated model ids")
    run.add_argument(
        "--rounds",
        type=int,
        default=None,
        help="rounds_cap override (default: YAML rounds_cap)",
    )
    run.add_argument(
        "--budget",
        type=float,
        default=None,
        help="budget_per_question_usd override",
    )
    run.add_argument(
        "--replicate",
        type=int,
        default=0,
        help="independent replicate id (fresh cache namespace unless --cache-mode replay)",
    )
    run.add_argument(
        "--cache-mode",
        default="fresh",
        choices=["fresh", "replay"],
        dest="cache_mode",
        help="fresh = isolated replicate; replay = exact-cache hypothesis",
    )
    run.add_argument("--checkpoint", default=None, help="JSONL checkpoint path for resume")
    run.add_argument(
        "--judge-model",
        default=None,
        dest="judge_model",
        help="blind LLM judge (default: first YAML judge, never implicit chairman)",
    )
    run.add_argument(
        "--n-samples",
        type=int,
        default=None,
        dest="n_samples",
        help="request budget C for an isolated baseline; best_of_n uses max(C-1,1)+1 judge",
    )
    run.add_argument("--dataset", default=None, help="path to JSONL dataset override")
    run.add_argument("--limit", type=int, default=None, help="first N cases after offset")
    run.add_argument("--offset", type=int, default=0, help="skip first N cases")
    run.add_argument(
        "--kind",
        default=None,
        choices=["xor", "biased_premise", "bandwagon", "true_minority"],
        help="filter cases by kind",
    )
    run.add_argument(
        "--arms",
        default=None,
        help="comma-separated compare arms (default: all five)",
    )
    run.add_argument(
        "--tables",
        default="both",
        choices=["both", "compute", "request", "cost"],
        help="request-count and/or cost tables (compute is an alias of request)",
    )
    run.add_argument("--dry-run", action="store_true", help="use deterministic mock engine")
    run.add_argument("--config", default="zhoda.yaml", help="council YAML for live arms")
    run.add_argument("--cache-path", default=None, dest="cache_path")
    run.add_argument("--transcripts-dir", default=None, dest="transcripts_dir")
    run.add_argument(
        "--clarify",
        default="no-clarify",
        choices=["smart", "no-clarify", "auto-clarify"],
        help="Stage 0 mode for Zhoda/majority arms (default no-clarify)",
    )
    run.add_argument("--quiet", action="store_true", help="do not print per-result lines")
    run.add_argument(
        "--shared-cache",
        action="store_true",
        help="one sqlite for all arms (vote then reuses debate positions)",
    )
    run.add_argument(
        "--judge",
        choices=["heuristic", "llm"],
        default="heuristic",
        help="headline accuracy: keyword or blind LLM (arm name hidden)",
    )
    run.add_argument("--out", default=None, help="write JSON report to this path")
    run.set_defaults(func=_cmd_run)

    rescore = sub.add_parser("rescore", help="blind-LLM rescore of a saved report")
    rescore.add_argument("--report", required=True, help="JSON report from `run`")
    rescore.add_argument("--config", default="zhoda.yaml")
    rescore.add_argument("--cache-path", default=None, dest="cache_path")
    rescore.add_argument("--out", default=None, help="write here (default: overwrite --report)")
    rescore.set_defaults(func=_cmd_rescore)

    exp = sub.add_parser("export-reputation", help="export the domain ELO matrix")
    exp.add_argument("--path", default=None, help="reputation store path override")
    exp.add_argument("--output", default=None, help="write JSON to this path")
    exp.set_defaults(func=_cmd_export_reputation)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
