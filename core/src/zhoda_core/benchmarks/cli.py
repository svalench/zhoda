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
from typing import List, Optional, Sequence, Tuple

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

DEFAULT_MODELS = (
    "meta-llama/llama-3.3-70b-instruct:free",
    "deepseek/deepseek-chat-v3-0324:free",
    "qwen/qwen3-235b-a22b:free",
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

    if args.tables == "compute":
        tables: Tuple[str, ...] = (MATCH_COMPUTE,)
    elif args.tables == "cost":
        tables = (MATCH_COST,)
    else:
        tables = (MATCH_COMPUTE, MATCH_COST)

    arms = None
    council_models: List[str] | None = None
    if not args.dry_run:
        try:
            from zhoda_core.config import load_council_config

            arms = _build_arms(
                args.config,
                args.clarify,
                rounds_cap=args.rounds,
                cache_path=args.cache_path,
                transcripts_dir=args.transcripts_dir,
                isolate_cache=not args.shared_cache,
            )
            council_models = [str(m) for m in load_council_config(args.config)["council"]]
        except Exception as exc:  # noqa: BLE001 — CLI boundary: YAML/key/config
            print(
                f"zhoda-core engine not available: {exc}\n"
                "re-run with --dry-run to validate the pipeline on mock profiles",
                file=sys.stderr,
            )
            return 2

    models: List[str] = models_arg or council_models or list(DEFAULT_MODELS)

    blind_judge = None
    if args.judge == "llm" and not args.dry_run:
        from zhoda_core.config import load_council_config, make_provider

        from .engine import arm_cache_path
        from .judge import BlindLlmJudge

        cfg = dict(load_council_config(args.config))
        base = args.cache_path or cfg.get("cache_path") or ".zhoda-cache.db"
        cfg["cache_path"] = arm_cache_path(str(base), "judge")
        chairman = str(cfg.get("chairman") or cfg["council"][0])
        blind_judge = BlindLlmJudge(make_provider(cfg), chairman)

    runner = ComparativeRunner(
        arms=arms,
        compare_modes=compare_modes,
        tables=tables,
        on_result=None if args.quiet else _on_result,
        blind_judge=blind_judge,
    )
    results = asyncio.run(
        runner.run_suite(
            cases,
            models,
            mode=args.mode,
            rounds=args.rounds,
            n_samples=args.n_samples,
        )
    )
    tables_out = summarize_tables(results)

    report = {
        "suite": args.suite,
        "mode": args.mode,
        "rounds": args.rounds,
        "models": models,
        "dry_run": args.dry_run,
        "judge": args.judge,
        "shared_cache": args.shared_cache,
        "arms": list(compare_modes),
        "tables_requested": list(tables),
        "n_cases": len(cases),
        "case_ids": [c.id for c in cases],
        "tables": tables_out,
        "summary": tables_out.get("compute_matched") or tables_out.get("cost_matched"),
        "results": results_to_dicts(results),
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
    from .judge import BlindLlmJudge

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
        judge = BlindLlmJudge(make_provider(cfg), chairman)
    except Exception as exc:  # noqa: BLE001
        print(f"zhoda-core engine not available: {exc}", file=sys.stderr)
        return 2

    async def _run() -> None:
        for row in report["results"]:
            if row.get("correct_heuristic") is None:
                row["correct_heuristic"] = row.get("correct")
            case = cases[str(row["case_id"])]
            ok, picked = await judge.score(case, str(row["decision"]))
            row["correct"] = ok
            row["judge_picked"] = picked
            print(
                f"{row['case_id']}\t{row['mode']}\t"
                f"heur={row['correct_heuristic']}\tllm={ok}\tpick={picked}",
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
    report["summary"] = tables_out.get("compute_matched") or tables_out.get("cost_matched")
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
    run.add_argument("--rounds", type=int, default=3)
    run.add_argument(
        "--n-samples",
        type=int,
        default=None,
        dest="n_samples",
        help="compute budget C for an isolated baseline; best_of_n uses max(C-1,1)+1 judge",
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
        choices=["both", "compute", "cost"],
        help="which matching tables to spend (default both)",
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
