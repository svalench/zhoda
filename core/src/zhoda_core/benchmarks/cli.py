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
from typing import List, Optional

from .datasets import builtin_cases, load_cases
from .metrics import summarize_tables
from .runner import ALL_MODES, ComparativeRunner, DeliberationEngine, results_to_dicts

DEFAULT_MODELS = (
    "meta-llama/llama-3.3-70b-instruct:free",
    "deepseek/deepseek-chat-v3-0324:free",
    "qwen/qwen3-235b-a22b:free",
)

_MODE_CHOICES = ("compare", *ALL_MODES)


def _build_arms(config: str, clarify_mode: str) -> dict[str, DeliberationEngine]:
    """Реальный engine; исключения наружу — CLI печатает и выходит 2."""
    from .engine import build_live_arms

    return build_live_arms(config, clarify_mode=clarify_mode)


def _cmd_run(args: argparse.Namespace) -> int:
    cases = load_cases(args.dataset) if args.dataset else builtin_cases(args.suite)
    if not cases:
        print("no benchmark cases found", file=sys.stderr)
        return 2

    models: List[str] = (
        [m.strip() for m in args.models.split(",") if m.strip()]
        if args.models
        else list(DEFAULT_MODELS)
    )

    arms = None
    if not args.dry_run:
        try:
            arms = _build_arms(args.config, args.clarify)
        except Exception as exc:  # noqa: BLE001 — CLI boundary: YAML/key/config
            print(
                f"zhoda-core engine not available: {exc}\n"
                "re-run with --dry-run to validate the pipeline on mock profiles",
                file=sys.stderr,
            )
            return 2

    runner = ComparativeRunner(arms=arms)
    results = asyncio.run(
        runner.run_suite(
            cases,
            models,
            mode=args.mode,
            rounds=args.rounds,
            n_samples=args.n_samples,
        )
    )
    tables = summarize_tables(results)

    report = {
        "suite": args.suite,
        "mode": args.mode,
        "rounds": args.rounds,
        "models": models,
        "dry_run": args.dry_run,
        "tables": tables,
        "summary": tables["compute_matched"],
        "results": results_to_dicts(results),
    }

    for label, summary in tables.items():
        print(f"=== {label} ===")
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
    run.add_argument("--suite", choices=["sycophancy", "minority", "all"], default="all")
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
    run.add_argument("--dry-run", action="store_true", help="use deterministic mock engine")
    run.add_argument("--config", default="zhoda.yaml", help="council YAML for live arms")
    run.add_argument(
        "--clarify",
        default="no-clarify",
        choices=["smart", "no-clarify", "auto-clarify"],
        help="Stage 0 mode for Zhoda/majority arms (default no-clarify)",
    )
    run.add_argument("--out", default=None, help="write JSON report to this path")
    run.set_defaults(func=_cmd_run)

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
