#!/usr/bin/env python3
"""Live G: Oxford vs short_review vs majority. Кап эксперимента $8. Без freeze-manifest."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT_DIR = Path(__file__).resolve().parent
REPO_ROOT = OUT_DIR.parents[2]
CORE_DIR = REPO_ROOT / "core"
CONFIG_PATH = CORE_DIR / "zhoda.yaml"
SPEND_CAP_USD = 8.0
REPLICATE_ID = 0
CACHE_MODE = "fresh"
CLARIFY_MODE = "no-clarify"

PROGRESS_PATH = OUT_DIR / "progress.json"
HEARTBEAT_PATH = OUT_DIR / "heartbeat.log"
CHECKPOINT_PATH = OUT_DIR / "checkpoint.jsonl"
REPORT_PATH = OUT_DIR / "report.json"
NOTES_PATH = OUT_DIR / "NOTES.md"
LOCK_PATH = OUT_DIR / "live_g.lock"
CACHE_PATH = OUT_DIR / "cache.db"
TRANSCRIPTS_DIR = OUT_DIR / "transcripts"


def _utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _log(msg: str) -> None:
    line = f"{_utc()} {msg}"
    print(line, flush=True)
    HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HEARTBEAT_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _acquire_lock() -> None:
    if LOCK_PATH.exists():
        raw = LOCK_PATH.read_text(encoding="utf-8").strip()
        try:
            pid = int(raw)
        except ValueError:
            pid = 0
        if pid:
            try:
                os.kill(pid, 0)
            except OSError:
                pass
            else:
                raise SystemExit(f"Live G already running pid={pid}")
    LOCK_PATH.write_text(str(os.getpid()), encoding="utf-8")


def _release_lock() -> None:
    try:
        if LOCK_PATH.exists() and LOCK_PATH.read_text(encoding="utf-8").strip() == str(
            os.getpid()
        ):
            LOCK_PATH.unlink()
    except OSError:
        return


def unique_arm_rows(results: list[Any]) -> list[Any]:
    """Одна строка на (case, arm): request-таблица, без двойного суммирования USD."""
    picked: dict[tuple[str, str], Any] = {}
    for row in results:
        key = (row.case_id, row.mode)
        if key not in picked or row.match == "request":
            picked[key] = row
    return list(picked.values())


def spent_usd(results: list[Any]) -> float:
    return float(sum(float(row.usd or 0.0) for row in unique_arm_rows(results)))


async def compare_case_resilient(runner: Any, case: Any, models: list[str], rounds: int) -> list[Any]:
    """Три руки независимо. Freeze/strip на одной не выкидывает кейс целиком."""
    from zhoda_core.benchmarks.runner import (
        MATCH_REQUEST,
        MODE_MAJORITY,
        MODE_SHORT_REVIEW,
        MODE_ZHODA,
        CaseResult,
    )
    from zhoda_core.providers.openrouter import QuotaExceededError

    async def one(mode: str) -> Any:
        try:
            return await runner.run_case(case, models, mode, rounds, match=MATCH_REQUEST)
        except QuotaExceededError:
            raise
        except Exception as exc:  # noqa: BLE001 — граница live-драйвера
            _log(f"ARM {mode} {case.id} err={type(exc).__name__}: {exc}")
            return CaseResult(
                case_id=case.id,
                suite=case.suite,
                kind=case.kind,
                mode=mode,
                decision="",
                match=MATCH_REQUEST,
                coverage_status="failed",
                skip_reason=f"{type(exc).__name__}: {exc}"[:300],
                spec_hash=runner.spec_hash,
                replicate_id=runner.replicate_id,
            )

    zhoda = await one(MODE_ZHODA)
    results: list[Any] = []
    if zhoda.coverage_status == "ok":
        results.extend(runner._emit_match_copy(zhoda))
    else:
        results.append(zhoda)
    compute = max(zhoda.requests, 1)
    majority = await one(MODE_MAJORITY)
    if majority.coverage_status == "ok":
        results.append(runner._qualify_request(majority, compute, 1))
    else:
        results.append(majority)
    short = await one(MODE_SHORT_REVIEW)
    if short.coverage_status == "ok":
        results.append(runner._qualify_request(short, compute, 1))
    else:
        results.append(short)
    return results


def evaluator_usd(results: list[Any]) -> float:
    total = 0.0
    seen: set[tuple[str, str]] = set()
    for row in results:
        if isinstance(row, dict):
            key = (str(row.get("case_id")), str(row.get("mode")))
            usage = row.get("evaluator_usage") or {}
        else:
            key = (row.case_id, row.mode)
            usage = row.evaluator_usage or {}
        if key in seen:
            continue
        seen.add(key)
        raw = usage.get("usd")
        if raw is not None:
            total += float(raw)
    return total


async def score_with_gold(
    decision: str,
    coverage: str,
    gold: Any,
    case_public: Any,
    *,
    provider: Any,
    model: str,
    judge_overlap: tuple[str, ...] = (),
) -> dict[str, Any]:
    from zhoda_core.eval.grading import score_with_gold as _score

    return await _score(
        decision,
        coverage,
        gold,
        case_public,
        provider=provider,
        model=model,
        judge_overlap=judge_overlap,
    )


async def overlay_gold(
    results: list[Any],
    gold_map: dict[str, Any],
    public_by_id: dict[str, Any],
    *,
    provider: Any,
    model: str,
    judge_overlap: tuple[str, ...] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from zhoda_core.eval.grading import overlay_scored_rows

    return await overlay_scored_rows(
        unique_arm_rows(results),
        gold_map,
        public_by_id,
        provider=provider,
        model=model,
        judge_overlap=judge_overlap,
    )


def paired_primary(scored: list[dict[str, Any]]) -> dict[str, Any]:
    from zhoda_core.eval.rescore import paired_primary as _paired

    return _paired(scored)


def decide_rule(primary: dict[str, Any], n_complete: int, bad_share: float) -> dict[str, Any]:
    from zhoda_core.eval.rescore import decide_rule as _decide

    return _decide(primary, n_complete, bad_share)


def coverage_stats(scored: list[dict[str, Any]], n_cases: int) -> dict[str, Any]:
    from zhoda_core.eval.rescore import coverage_stats as _cov

    return _cov(scored, n_cases)


def write_progress(payload: dict[str, Any]) -> None:
    PROGRESS_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )


def write_notes(report: dict[str, Any]) -> None:
    primary = report["primary"]
    rule = report["decision_rule"]
    cov = report["coverage"]
    delta = primary.get("delta")
    delta_s = "n/a" if delta is None else f"{delta:.4f}"
    lines = [
        "# Live G — Oxford vs short_review (pilot holdout)",
        "",
        f"Started: {report['started_at']}",
        f"Stopped: {report['stopped_at']}",
        f"Stop reason: `{report['stop_reason']}`",
        f"Execution SHA: `{report['execution_sha']}`",
        f"Frozen source_sha (identity): `{report['frozen']['source_sha']}`",
        f"Spend: ${report['spend_usd']:.4f} / ${SPEND_CAP_USD:.2f}",
        f"n complete (3 arms): {cov['n_complete']} / {cov['n_cases_in_suite']}",
        f"Ungraded/failed share: {cov['ungraded_failed_share']:.3f}",
        f"Primary Δ = action_correct(short_review) − action_correct(oxford): **{delta_s}**",
        f"Decision rule: **{rule['verdict']}** — {rule['reason']}",
        f"Product default in code: **{rule['product_default']}** (not flipped by this script).",
        "",
        "Independent validation: **false**. Gold is provisional / protocol-author-not-independent.",
        "No p-value. Grader: `pilot-grader.v2` (YAML judges[0], evaluator_usage). "
        "Keyword heuristic is kept as `action_correct_heuristic`.",
        "Do not retune prompts on these holdout outputs.",
        "",
    ]
    NOTES_PATH.write_text("\n".join(lines), encoding="utf-8")


def verify_freeze() -> dict[str, Any]:
    from zhoda_core.benchmarks.spec import hash_prompts, hash_rubric
    from zhoda_core.eval.pilot import (
        FROZEN_MANIFEST,
        GOLD_JSONL,
        PLAN_MD,
        PUBLIC_JSONL,
        file_hash,
    )

    frozen = json.loads(FROZEN_MANIFEST.read_text(encoding="utf-8"))
    checks = {
        "public_hash": file_hash(PUBLIC_JSONL) == frozen["public_hash"],
        "gold_hash": file_hash(GOLD_JSONL) == frozen["gold_hash"],
        "plan_hash": file_hash(PLAN_MD) == frozen["plan_hash"],
        "prompt_hash": hash_prompts() == frozen["prompt_hash"],
        "rubric_hash": hash_rubric() == frozen["rubric_hash"],
    }
    if not checks["public_hash"] or not checks["gold_hash"] or not checks["plan_hash"]:
        raise SystemExit(f"frozen dataset/plan hash mismatch: {checks}")
    return {"frozen": frozen, "hash_ok": checks}


def self_check() -> int:
    from zhoda_core.env import load_zhoda_env

    load_zhoda_env(REPO_ROOT)
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("missing OPENROUTER_API_KEY", file=sys.stderr)
        return 2
    if not CONFIG_PATH.exists():
        print(f"missing {CONFIG_PATH}", file=sys.stderr)
        return 2
    info = verify_freeze()
    print(json.dumps({"ok": True, "hash_ok": info["hash_ok"], "has_key": True}, ensure_ascii=False))
    return 0


async def run_live() -> dict[str, Any]:
    from zhoda_core.benchmarks.checkpoint import CheckpointStore
    from zhoda_core.benchmarks.cli import _build_arms
    from zhoda_core.benchmarks.runner import (
        MATCH_REQUEST,
        PILOT_ARMS,
        CaseResult,
        ComparativeRunner,
        results_to_dicts,
    )
    from zhoda_core.benchmarks.spec import resolve_run_spec, source_sha
    from zhoda_core.benchmarks.spy import CallSpy
    from zhoda_core.config import load_council_config, make_provider
    from zhoda_core.env import load_zhoda_env
    from zhoda_core.eval.pilot import load_public_cases, public_to_benchmark
    from zhoda_core.providers.openrouter import QuotaExceededError

    load_zhoda_env(REPO_ROOT)
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise SystemExit("missing OPENROUTER_API_KEY")
    freeze_info = verify_freeze()
    yaml_cfg = load_council_config(CONFIG_PATH)
    public_cases = load_public_cases()
    cases = [public_to_benchmark(item) for item in public_cases]
    spec = resolve_run_spec(
        cases=cases,
        yaml_cfg=yaml_cfg,
        models_override=None,
        rounds_override=None,
        budget_override=None,
        cache_path=str(CACHE_PATH),
        isolate_cache=True,
        cache_mode=CACHE_MODE,
        replicate_id=REPLICATE_ID,
        clarify_mode=CLARIFY_MODE,
        dry_run=False,
        source_dir=REPO_ROOT,
    )
    models = list(spec.roster.council)
    spies = {
        mode: CallSpy(spec.roster.allowed_models(), expected_max_tokens=spec.max_tokens)
        for mode in PILOT_ARMS
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    arms = _build_arms(
        str(CONFIG_PATH),
        CLARIFY_MODE,
        rounds_cap=spec.rounds,
        cache_path=str(CACHE_PATH),
        transcripts_dir=str(TRANSCRIPTS_DIR),
        isolate_cache=True,
        council=models,
        replicate_id=spec.replicate_id,
        cache_mode=spec.cache_mode,
        spies=spies,
        modes=PILOT_ARMS,
    )
    checkpoint = CheckpointStore(CHECKPOINT_PATH)
    runner = ComparativeRunner(
        arms=arms,
        compare_modes=PILOT_ARMS,
        tables=(MATCH_REQUEST,),
        on_result=None,
        blind_judge=None,
        spec_hash=spec.spec_hash,
        replicate_id=spec.replicate_id,
        checkpoint=checkpoint,
        n_council=len(spec.roster.council),
    )
    started = _utc()
    results: list[CaseResult] = []
    stop_reason = "complete"
    stop_error = ""
    _log(
        f"LIVE_G_START n={len(cases)} arms={list(PILOT_ARMS)} cap={SPEND_CAP_USD} "
        f"spec={spec.spec_hash[:12]} sha={source_sha(REPO_ROOT)[:12]}"
    )
    try:
        for index, case in enumerate(cases):
            spent = spent_usd(results)
            if spent >= SPEND_CAP_USD:
                stop_reason = "spend_cap"
                _log(f"LIVE_G_STOP reason=spend_cap spent={spent:.4f}")
                break
            _log(
                f"CASE {index + 1}/{len(cases)} id={case.id} spent={spent:.4f} "
                f"remain={SPEND_CAP_USD - spent:.4f}"
            )
            try:
                batch = await compare_case_resilient(runner, case, models, spec.rounds)
            except QuotaExceededError as exc:
                stop_reason = "quota_exceeded"
                stop_error = str(exc)
                _log(f"LIVE_G_STOP reason=quota_exceeded err={exc}")
                break
            except Exception as exc:  # noqa: BLE001 — кейс не валит весь G
                _log(f"CASE {case.id} err={type(exc).__name__}: {exc}")
                traceback.print_exc()
                continue
            results.extend(batch)
            spent = spent_usd(results)
            write_progress(
                {
                    "status": "running",
                    "case_index": index + 1,
                    "case_id": case.id,
                    "n_cases": len(cases),
                    "spent_usd": spent,
                    "spend_cap_usd": SPEND_CAP_USD,
                    "n_rows": len(unique_arm_rows(results)),
                    "updated_at": _utc(),
                }
            )
            _log(
                f"CASE_DONE {case.id} spent={spent:.4f} "
                f"rows={len(unique_arm_rows(results))}"
            )
            if spent >= SPEND_CAP_USD:
                stop_reason = "spend_cap"
                _log(f"LIVE_G_STOP reason=spend_cap spent={spent:.4f}")
                break
        else:
            stop_reason = "complete"
            _log("LIVE_G_STOP reason=complete")
    except Exception as exc:  # noqa: BLE001 — граница live-драйвера
        stop_reason = "error"
        stop_error = f"{type(exc).__name__}: {exc}"
        _log(f"LIVE_G_STOP reason=error err={stop_error}")
        traceback.print_exc()

    spy_error = ""
    try:
        for spy in spies.values():
            spy.verify()
    except Exception as exc:  # noqa: BLE001
        spy_error = str(exc)
        _log(f"spy.verify failed: {exc}")

    from zhoda_core.eval.gold import load_gold
    from zhoda_core.eval.grading import GRADER_VERSION
    from zhoda_core.eval.pilot import GOLD_JSONL

    gold_map = load_gold(GOLD_JSONL)
    if not spec.roster.judge_model:
        raise SystemExit("no yaml judges[0] — refusing chairman as grader")
    public_by_id = {item.id: item for item in public_cases}
    eval_cfg = dict(yaml_cfg)
    eval_cfg["cache_path"] = str(OUT_DIR / "cache-grader.db")
    eval_provider = make_provider(eval_cfg)
    scored, disagreements = await overlay_gold(
        results,
        gold_map,
        public_by_id,
        provider=eval_provider,
        model=spec.roster.judge_model,
        judge_overlap=spec.roster.judge_overlap,
    )
    cov = coverage_stats(scored, len(cases))
    primary = paired_primary(scored)
    rule = decide_rule(primary, cov["n_complete"], cov["ungraded_failed_share"])
    spend = spent_usd(results)
    report = {
        "schema": "zhoda.eval.live_g.v1",
        "live": True,
        "independent_validation": False,
        "started_at": started,
        "stopped_at": _utc(),
        "stop_reason": stop_reason,
        "stop_error": stop_error,
        "spy_error": spy_error,
        "execution_sha": source_sha(REPO_ROOT),
        "frozen": freeze_info["frozen"],
        "hash_ok": freeze_info["hash_ok"],
        "spend_cap_usd": SPEND_CAP_USD,
        "spend_usd": spend,
        "engine_usd": spend,
        "evaluator_usd": evaluator_usd(scored),
        "clarify_mode": CLARIFY_MODE,
        "cache_mode": CACHE_MODE,
        "replicate_id": REPLICATE_ID,
        "arms": list(PILOT_ARMS),
        "tables": ["request"],
        "judge": GRADER_VERSION,
        "blind_llm_judge": True,
        "grader_version": GRADER_VERSION,
        "heuristic_llm_disagreements": disagreements,
        "n_cases": len(cases),
        "case_ids": [c.id for c in cases],
        "manifest": spec.to_manifest(),
        "yaml_roster": {
            "council": list(spec.roster.council),
            "judges": list(spec.roster.judges),
            "chairman": spec.roster.chairman,
            "classifiers": list(spec.roster.classifiers),
            "rounds_cap": spec.rounds,
            "budget_per_question_usd": spec.budget_per_question_usd,
            "judge_overlap": list(spec.roster.judge_overlap),
        },
        "coverage": cov,
        "primary": {
            k: v for k, v in primary.items() if k != "pairs"
        },
        "pairs": primary["pairs"],
        "decision_rule": rule,
        "results": scored,
        "raw_results": results_to_dicts(results),
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_notes(report)
    write_progress(
        {
            "status": "stopped",
            "stop_reason": stop_reason,
            "spent_usd": spend,
            "n_complete": cov["n_complete"],
            "delta": primary.get("delta"),
            "verdict": rule["verdict"],
            "updated_at": _utc(),
        }
    )
    _log(
        f"LIVE_G_DONE reason={stop_reason} spent={spend:.4f} "
        f"n_complete={cov['n_complete']} delta={primary.get('delta')} "
        f"verdict={rule['verdict']}"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--self-check"]:
        return self_check()
    _acquire_lock()
    try:
        asyncio.run(run_live())
    finally:
        _release_lock()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
