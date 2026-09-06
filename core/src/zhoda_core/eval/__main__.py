"""CLI: validate / dry-run / freeze-manifest / status. Без live API."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

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
    results = asyncio.run(
        runner.run_suite(cases, ["m1", "m2", "m3"], mode="compare", rounds=4)
    )
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="zhoda-eval")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="schema + gold/public id join")
    sub.add_parser("status", help="print READY_FOR_APPROVAL")
    sub.add_parser("freeze-manifest", help="write frozen hashes (no live)")
    dry = sub.add_parser("dry-run", help="offline mock on public cases only")
    dry.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    if args.command == "validate":
        return _cmd_validate(args)
    if args.command == "status":
        return _cmd_status(args)
    if args.command == "freeze-manifest":
        return _cmd_freeze(args)
    if args.command == "dry-run":
        return _cmd_dry_run(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
