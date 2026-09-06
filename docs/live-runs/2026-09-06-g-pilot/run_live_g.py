#!/usr/bin/env python3
"""Live G: Oxford vs short_review vs majority. Кап эксперимента $8. Без freeze-manifest."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
from collections import defaultdict
from dataclasses import asdict
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


def evaluator_usd(results: list[Any]) -> float:
    total = 0.0
    seen: set[tuple[str, str]] = set()
    for row in unique_arm_rows(results):
        key = (row.case_id, row.mode)
        if key in seen:
            continue
        seen.add(key)
        usage = row.evaluator_usage or {}
        raw = usage.get("usd")
        if raw is not None:
            total += float(raw)
    return total


def _map_gold_id(expected: str, options: tuple[str, ...]) -> str:
    if not options:
        return expected
    fold_exp = expected.casefold()
    for opt in options:
        if opt.casefold() == fold_exp:
            return opt
        if fold_exp.startswith(opt.casefold() + ";"):
            return opt
    return expected


def score_with_gold(decision: str, coverage: str, gold: Any, options: tuple[str, ...]) -> dict[str, Any]:
    """Gold после attempts. Не в engine. Ungraded/failed в знаменателе, без зачёта."""
    from zhoda_core.benchmarks.judge import pick_matches_gold
    from zhoda_core.benchmarks.quality import _ABSTAIN_RE, extract_chosen_action

    failed = coverage in {"failed", "skipped", "infeasible"} or not (decision or "").strip()
    abstain = bool(_ABSTAIN_RE.search(decision or ""))
    labels = list(options)
    gold_id = _map_gold_id(gold.expected_action, options)
    if gold_id not in labels and options:
        labels = [gold_id, *labels]
    elif not labels:
        labels = [gold.expected_action, *gold.allowed_alternatives]
    unacc = [
        claim
        for claim in gold.unacceptable_claims
        if claim and claim.casefold() in (decision or "").casefold()
    ]
    chosen: str | None
    action_correct: bool | None
    if gold.abstain_policy == "required":
        chosen = None
        action_correct = False if failed else bool(abstain)
    elif failed:
        chosen = None
        action_correct = False
    elif abstain:
        chosen = None
        action_correct = False
    else:
        chosen = extract_chosen_action(decision, tuple(labels))
        if not chosen:
            action_correct = None
        else:
            ok = pick_matches_gold(chosen, gold_id, labels)
            for alt in gold.allowed_alternatives:
                alt_id = _map_gold_id(alt, tuple(labels))
                if pick_matches_gold(chosen, alt_id, labels):
                    ok = True
            action_correct = ok
    appropriate: bool | None
    if gold.abstain_policy == "required":
        appropriate = bool(abstain) and not failed
    elif gold.abstain_policy == "forbidden":
        appropriate = (not abstain) if not failed else False
    else:
        appropriate = None
    return {
        "chosen_action": chosen,
        "action_correct": action_correct,
        "ungraded": action_correct is None,
        "unacceptable_hit": bool(unacc),
        "abstain": abstain,
        "appropriate_abstention": appropriate,
        "label_status": gold.label_status,
        "annotator": gold.annotator,
    }


def overlay_gold(results: list[Any], gold_map: dict[str, Any], cases: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in unique_arm_rows(results):
        payload = asdict(row)
        gold = gold_map[row.case_id]
        case = cases[row.case_id]
        scored = score_with_gold(
            row.decision, row.coverage_status, gold, case.answer_options,
        )
        payload["action_correct_heuristic"] = row.action_correct
        payload["action_correct"] = scored["action_correct"]
        payload["chosen_action"] = scored["chosen_action"]
        payload["appropriate_abstention"] = scored["appropriate_abstention"]
        payload["unacceptable_hit"] = scored["unacceptable_hit"]
        payload["gold_after_attempts"] = True
        payload["ungraded"] = scored["ungraded"]
        rows.append(payload)
    return rows


def _credit(value: bool | None) -> float:
    return 1.0 if value is True else 0.0


def paired_primary(scored: list[dict[str, Any]]) -> dict[str, Any]:
    by_case: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in scored:
        by_case[str(row["case_id"])][str(row["mode"])] = row
    paired: list[dict[str, Any]] = []
    for case_id, arms in sorted(by_case.items()):
        ox = arms.get("zhoda")
        sr = arms.get("short_review")
        if ox is None or sr is None:
            continue
        if ox.get("coverage_status") != "ok" or sr.get("coverage_status") != "ok":
            continue
        ox_c = _credit(ox.get("action_correct"))  # type: ignore[arg-type]
        sr_c = _credit(sr.get("action_correct"))  # type: ignore[arg-type]
        kind = str(ox.get("kind") or "")
        paired.append(
            {
                "case_id": case_id,
                "kind": kind,
                "oxford_correct": ox_c,
                "short_review_correct": sr_c,
                "delta": sr_c - ox_c,
                "oxford_usd": float(ox.get("usd") or 0.0),
                "short_review_usd": float(sr.get("usd") or 0.0),
                "majority_usd": float((arms.get("majority") or {}).get("usd") or 0.0),
            }
        )
    n = len(paired)
    delta = sum(p["delta"] for p in paired) / n if n else None
    mean_sr = sum(p["short_review_usd"] for p in paired) / n if n else None
    mean_ox = sum(p["oxford_usd"] for p in paired) / n if n else None
    by_kind: dict[str, list[float]] = defaultdict(list)
    for row in paired:
        by_kind[row["kind"]].append(row["delta"])
    class_delta = {
        kind: {
            "n": len(vals),
            "delta": sum(vals) / len(vals),
        }
        for kind, vals in sorted(by_kind.items())
    }
    return {
        "n_paired": n,
        "delta": delta,
        "mean_usd_short_review": mean_sr,
        "mean_usd_oxford": mean_ox,
        "class_delta": class_delta,
        "pairs": paired,
    }


def decide_rule(primary: dict[str, Any], n_complete: int, bad_share: float) -> dict[str, Any]:
    """Правило из preregistration. Не p-value. Product default здесь не меняем."""
    if n_complete < 30 or bad_share > 0.20:
        return {
            "verdict": "inconclusive",
            "product_default": "debate",
            "reason": (
                f"rule1: n_complete={n_complete} < 30 or ungraded/failed share "
                f"{bad_share:.3f} > 0.20"
            ),
        }
    delta = primary["delta"]
    mean_sr = primary["mean_usd_short_review"]
    mean_ox = primary["mean_usd_oxford"]
    if delta is None:
        return {
            "verdict": "inconclusive",
            "product_default": "debate",
            "reason": "no paired Δ",
        }
    class_notes = []
    for kind, info in (primary.get("class_delta") or {}).items():
        if info["n"] >= 5 and info["delta"] < -0.15:
            class_notes.append(
                f"{kind}: oxford advantage {-info['delta']:.3f} (n={info['n']})"
            )
    if abs(delta) <= 0.10 and mean_sr is not None and mean_ox is not None and mean_sr < mean_ox:
        return {
            "verdict": "recommend_short_review_default",
            "product_default": "debate",
            "reason": (
                f"rule2: |Δ|={abs(delta):.3f}≤0.10 and mean USD short_review "
                f"{mean_sr:.4f} < oxford {mean_ox:.4f}; code default unchanged until owner"
            ),
            "class_oxford_opt_in": class_notes,
        }
    if delta < -0.10:
        return {
            "verdict": "keep_debate_default",
            "product_default": "debate",
            "reason": f"rule3: Δ={delta:.3f} < -0.10 (oxford wins primary)",
            "class_oxford_opt_in": class_notes,
        }
    if delta > 0.10:
        return {
            "verdict": "short_review_wins_primary",
            "product_default": "debate",
            "reason": (
                f"Δ={delta:.3f} > 0.10; short_review лучше по primary. "
                "Default в коде не меняем без отдельного решения owner."
            ),
            "class_oxford_opt_in": class_notes,
        }
    return {
        "verdict": "keep_debate_default",
        "product_default": "debate",
        "reason": (
            f"|Δ|={abs(delta):.3f}≤0.10 but short_review is not cheaper; keep debate"
        ),
        "class_oxford_opt_in": class_notes,
    }


def coverage_stats(scored: list[dict[str, Any]], n_cases: int) -> dict[str, Any]:
    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in scored:
        by_case[str(row["case_id"])].append(row)
    complete = 0
    for rows in by_case.values():
        modes = {str(r["mode"]): r for r in rows}
        if all(
            modes.get(m, {}).get("coverage_status") == "ok"
            for m in ("zhoda", "short_review", "majority")
        ):
            complete += 1
    n_attempts = len(scored)
    bad = 0
    for row in scored:
        if row.get("coverage_status") != "ok" or row.get("ungraded") or row.get("action_correct") is None:
            bad += 1
    share = (bad / n_attempts) if n_attempts else 1.0
    return {
        "n_cases_in_suite": n_cases,
        "n_cases_touched": len(by_case),
        "n_complete": complete,
        "n_attempts": n_attempts,
        "n_failed_or_ungraded": bad,
        "ungraded_failed_share": share,
    }


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
        "No p-value. Blind LLM judge was not spent during the engine loop (gold sidecar after attempts).",
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
    from zhoda_core.config import load_council_config
    from zhoda_core.env import load_zhoda_env
    from zhoda_core.eval.pilot import load_public_cases, public_to_benchmark
    from zhoda_core.providers.openrouter import (
        BudgetExceededError,
        QuotaExceededError,
        ZhodaProviderError,
    )

    load_zhoda_env(REPO_ROOT)
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise SystemExit("missing OPENROUTER_API_KEY")
    freeze_info = verify_freeze()
    yaml_cfg = load_council_config(CONFIG_PATH)
    public_cases = load_public_cases()
    cases = [public_to_benchmark(item) for item in public_cases]
    case_by_id = {c.id: c for c in cases}
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
                batch = await runner._run_compare_case(case, models, spec.rounds)
            except QuotaExceededError as exc:
                stop_reason = "quota_exceeded"
                stop_error = str(exc)
                _log(f"LIVE_G_STOP reason=quota_exceeded err={exc}")
                break
            except BudgetExceededError as exc:
                _log(f"CASE {case.id} budget_exceeded (per-question) err={exc}")
                stop_error = str(exc)
                spent = spent_usd(results)
                if spent >= SPEND_CAP_USD:
                    stop_reason = "spend_cap"
                    _log(f"LIVE_G_STOP reason=spend_cap spent={spent:.4f}")
                    break
                continue
            except ZhodaProviderError as exc:
                stop_reason = "provider_hard_fail"
                stop_error = str(exc)
                _log(f"LIVE_G_STOP reason=provider_hard_fail err={exc}")
                break
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
    from zhoda_core.eval.pilot import GOLD_JSONL

    gold_map = load_gold(GOLD_JSONL)
    scored = overlay_gold(results, gold_map, case_by_id)
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
        "engine_usd": spend - evaluator_usd(results),
        "evaluator_usd": evaluator_usd(results),
        "clarify_mode": CLARIFY_MODE,
        "cache_mode": CACHE_MODE,
        "replicate_id": REPLICATE_ID,
        "arms": list(PILOT_ARMS),
        "tables": ["request"],
        "judge": "gold_sidecar_after_attempts",
        "blind_llm_judge": False,
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
