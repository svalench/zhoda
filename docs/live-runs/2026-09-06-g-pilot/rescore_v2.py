#!/usr/bin/env python3
"""Rescore live G report.json через pilot-grader.v2. Engine не вызываем.

Судья: yaml judges[0], кэш rescore-v2/judge-cache.db, кап $1.00.
report.json не переписывается.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT_DIR = Path(__file__).resolve().parent
REPO_ROOT = OUT_DIR.parents[2]
CORE_DIR = REPO_ROOT / "core"
CONFIG_PATH = CORE_DIR / "zhoda.yaml"
REPORT_V1 = OUT_DIR / "report.json"
REPORT_V2 = OUT_DIR / "report-v2.json"
CACHE_DIR = OUT_DIR / "rescore-v2"
CACHE_PATH = CACHE_DIR / "judge-cache.db"
FIRST_PASS = CACHE_DIR / "first-pass-usage.json"


def _utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _log(msg: str) -> None:
    print(f"{_utc()} {msg}", flush=True)


async def run_rescore() -> dict[str, Any]:
    from zhoda_core.config import load_council_config, make_provider
    from zhoda_core.env import load_zhoda_env
    from zhoda_core.eval.gold import load_gold
    from zhoda_core.eval.pilot import GOLD_JSONL, load_public_cases
    from zhoda_core.eval.pilot_rescore import (
        EVALUATOR_CAP_USD,
        SOURCE_REPORT_SHA256,
        JudgeOnlyProvider,
        judge_roster,
        rescore_report_v2,
    )

    load_zhoda_env(REPO_ROOT)
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise SystemExit("missing OPENROUTER_API_KEY")
    if REPORT_V2.resolve() == REPORT_V1.resolve():
        raise SystemExit("refusing to overwrite report.json")
    source_sha = _sha256(REPORT_V1)
    if source_sha != SOURCE_REPORT_SHA256:
        raise SystemExit(
            f"report.json hash {source_sha} != frozen {SOURCE_REPORT_SHA256}"
        )
    before = REPORT_V1.read_bytes()
    report = json.loads(before.decode("utf-8"))
    yaml_cfg = dict(load_council_config(CONFIG_PATH))
    model, overlap = judge_roster(yaml_cfg)
    council = {str(item) for item in (yaml_cfg.get("council") or [])}
    chairman = str(yaml_cfg.get("chairman") or "")
    if model in council or model == chairman:
        raise SystemExit(f"judges[0]={model} overlaps council/chairman")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    yaml_cfg["cache_path"] = str(CACHE_PATH)
    inner = make_provider(yaml_cfg, budget_usd=EVALUATOR_CAP_USD)
    provider = JudgeOnlyProvider(inner, model, cap_usd=EVALUATOR_CAP_USD)
    gold_map = load_gold(GOLD_JSONL)
    public_by_id = {item.id: item for item in load_public_cases()}
    _log(f"RESCORE_V2_START judge={model} cap={EVALUATOR_CAP_USD} n={len(report.get('results') or [])}")
    try:
        out = await rescore_report_v2(
            report,
            gold_map,
            public_by_id,
            provider=provider,
            model=model,
            judge_overlap=overlap,
            source_sha256=source_sha,
        )
    finally:
        await inner.close()
    if REPORT_V1.read_bytes() != before:
        raise SystemExit("report.json mutated during rescore")
    this_requests = int(out["evaluator_requests"])
    this_hits = int(out["evaluator_cache_hits"])
    this_usd = float(out["evaluator_usd"])
    out["this_run_evaluator_requests"] = this_requests
    out["this_run_evaluator_cache_hits"] = this_hits
    out["this_run_evaluator_usd"] = this_usd
    if this_requests > 0:
        FIRST_PASS.write_text(
            json.dumps(
                {
                    "evaluator_usd": this_usd,
                    "evaluator_requests": this_requests,
                    "evaluator_cache_hits": this_hits,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    elif FIRST_PASS.exists():
        first = json.loads(FIRST_PASS.read_text(encoding="utf-8"))
        out["evaluator_usd"] = first["evaluator_usd"]
        out["evaluator_requests"] = first["evaluator_requests"]
        out["evaluator_cache_hits"] = first["evaluator_cache_hits"]
    REPORT_V2.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _log(
        f"RESCORE_V2_DONE rows={out['n_rows']} paired={out['n_paired_fully_graded']} "
        f"this_run_requests={this_requests} this_run_cache_hits={this_hits} "
        f"first_pass_usd={out['evaluator_usd']:.4f} ungraded={len(out['dropped_ungraded'])}"
    )
    return out


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--self-check"]:
        from zhoda_core.env import load_zhoda_env
        from zhoda_core.eval.pilot_rescore import SOURCE_REPORT_SHA256

        load_zhoda_env(REPO_ROOT)
        digest = _sha256(REPORT_V1)
        ok = digest == SOURCE_REPORT_SHA256
        print(
            json.dumps(
                {
                    "ok": ok,
                    "source_sha256": digest,
                    "expected": SOURCE_REPORT_SHA256,
                    "has_key": bool(os.environ.get("OPENROUTER_API_KEY")),
                    "out": str(REPORT_V2),
                    "cache": str(CACHE_PATH),
                },
                ensure_ascii=False,
            )
        )
        return 0 if ok else 2
    asyncio.run(run_rescore())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
