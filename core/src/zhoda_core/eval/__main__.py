"""CLI: validate / dry-run / freeze-manifest / status / rescore-report.

rescore-report по умолчанию executable (без OpenRouter). --judge llm — только
если задание явно разрешает тратить evaluator budget.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from .pilot import (
    FROZEN_MANIFEST,
    GOLD_JSONL,
    PILOT_STATUS,
    PUBLIC_JSONL,
    dump_public,
    load_public_cases,
    public_to_benchmark,
    validate_public,
    write_frozen_manifest,
)


def _cmd_validate(_: argparse.Namespace) -> int:
    dump_public(PUBLIC_JSONL)
    from .gold import dump_gold

    dump_gold(GOLD_JSONL)
    errors = validate_public()
    gold_ids = {
        json.loads(line)["id"]
        for line in GOLD_JSONL.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    pub_ids = {c.id for c in load_public_cases()}
    if gold_ids != pub_ids:
        errors.append(f"gold/public id mismatch {sorted(gold_ids ^ pub_ids)[:8]}")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 2
    print(f"ok n={len(pub_ids)} status={PILOT_STATUS}")
    return 0


def _cmd_status(_: argparse.Namespace) -> int:
    print(PILOT_STATUS)
    print("live=false")
    print("independent_validation=false")
    print("do_not_run_without_owner_approval")
    return 0


def _cmd_freeze(_: argparse.Namespace) -> int:
    dump_public(PUBLIC_JSONL)
    from .gold import dump_gold

    dump_gold(GOLD_JSONL)
    payload = write_frozen_manifest()
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"wrote {FROZEN_MANIFEST}")
    return 0


def _cmd_dry_run(args: argparse.Namespace) -> int:
    """Offline mock. Не грузит gold. Не live accuracy."""
    os.environ.pop("OPENROUTER_API_KEY", None)
    from zhoda_core.benchmarks.runner import (
        PILOT_ARMS,
        ComparativeRunner,
        results_to_dicts,
    )
    from zhoda_core.benchmarks.spec import hash_prompts, hash_rubric

    cases = [public_to_benchmark(c) for c in load_public_cases()]
    runner = ComparativeRunner(compare_modes=PILOT_ARMS)
    results = asyncio.run(runner.run_suite(cases, ["m1", "m2", "m3"], mode="compare", rounds=4))
    report = {
        "status": PILOT_STATUS,
        "live": False,
        "independent_validation": False,
        "dry_run": True,
        "n_cases": len(cases),
        "case_ids": [c.id for c in cases],
        "arms": list(PILOT_ARMS),
        "prompt_hash": hash_prompts(),
        "rubric_hash": hash_rubric(),
        "results": results_to_dicts(results),
        "note": "ungraded offline mock; gold not loaded; not a completed study",
    }
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {out}")
    print(f"status={PILOT_STATUS} n={len(cases)} arms={list(PILOT_ARMS)} live=false")
    return 0


def _cmd_rescore_report(args: argparse.Namespace) -> int:
    """Пересчитать сохранённый report. Engine не вызываем. Источник не переписываем."""
    from .gold import load_gold
    from .rescore import rescore_report

    src = Path(args.report)
    out = Path(args.out)
    if src.resolve() == out.resolve():
        print(
            "refusing to overwrite source report; pass a new --out (e.g. report-v2.json)",
            file=sys.stderr,
        )
        return 2
    report = json.loads(src.read_text(encoding="utf-8"))
    gold_map = load_gold(GOLD_JSONL)
    cases = {c.id: public_to_benchmark(c) for c in load_public_cases()}
    missing = sorted({str(r["case_id"]) for r in report.get("results") or []} - set(cases))
    if missing:
        print(f"unknown case ids: {missing[:8]}", file=sys.stderr)
        return 2
    votes = None
    judge_name = args.judge
    if judge_name == "llm":
        try:
            votes = asyncio.run(_llm_votes(report, gold_map, cases, args.config))
        except (OSError, ValueError) as exc:
            print(f"judge llm unavailable: {exc}", file=sys.stderr)
            return 2
    scored = rescore_report(
        report,
        gold_map,
        cases,
        votes=votes,
        judge_name=judge_name,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(scored, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out} schema={scored.get('schema')} judge={judge_name}")
    return 0


async def _llm_votes(
    report: dict[str, Any],
    gold_map: dict[str, Any],
    cases: dict[str, Any],
    config_path: str,
) -> dict[tuple[str, str], Any]:
    """Blind judge по сохранённым decision. Engine не трогаем."""
    from zhoda_core.benchmarks.judge import BlindLlmJudge
    from zhoda_core.config import load_council_config, make_provider

    from .grader import allowed_labels
    from .pilot_rescore import judge_roster
    from .rescore import unique_arm_rows

    cfg = dict(load_council_config(config_path))
    model, overlap = judge_roster(cfg)
    judge = BlindLlmJudge(make_provider(cfg), model, overlap_roles=overlap)
    votes: dict[tuple[str, str], Any] = {}
    for row in unique_arm_rows(list(report.get("results") or [])):
        case_id = str(row["case_id"])
        mode = str(row["mode"])
        gold = gold_map[case_id]
        case = cases[case_id]
        votes[(case_id, mode)] = await judge.score_with_labels(
            case,
            str(row.get("decision") or ""),
            gold=gold.expected_action,
            allowed=allowed_labels(gold, case.answer_options),
        )
    return votes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="zhoda-eval")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="schema + gold/public id join")
    sub.add_parser("status", help="print READY_FOR_APPROVAL")
    sub.add_parser("freeze-manifest", help="write frozen hashes (no live)")
    dry = sub.add_parser("dry-run", help="offline mock on public cases only")
    dry.add_argument("--out", default=None)
    rescore = sub.add_parser(
        "rescore-report",
        help="offline gold-aware rescore of a saved report (new file, no engine)",
    )
    rescore.add_argument("--report", required=True)
    rescore.add_argument("--out", required=True)
    rescore.add_argument(
        "--judge",
        default="none",
        choices=["none", "llm"],
        help="none = executable (no HTTP); llm = blind sidecar judge (evaluator spend)",
    )
    rescore.add_argument("--config", default="zhoda.yaml")
    args = parser.parse_args(argv)
    if args.command == "validate":
        return _cmd_validate(args)
    if args.command == "status":
        return _cmd_status(args)
    if args.command == "freeze-manifest":
        return _cmd_freeze(args)
    if args.command == "dry-run":
        return _cmd_dry_run(args)
    if args.command == "rescore-report":
        return _cmd_rescore_report(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
