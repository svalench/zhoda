"""Offline rescore сохранённого report → новая версия. Engine не вызываем."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence

from zhoda_core.benchmarks.cache_guard import COST_CACHED, COST_EXACT, cost_status_for
from zhoda_core.benchmarks.datasets import BenchmarkCase
from zhoda_core.benchmarks.judge import GradeResult
from zhoda_core.eval.gold import GoldRow
from zhoda_core.eval.grader import PilotGrade, apply_gold

LIVE_G_V1 = "zhoda.eval.live_g.v1"
LIVE_G_V2 = "zhoda.eval.live_g.v2"


def overlay_row(
    row: Mapping[str, Any],
    gold: GoldRow,
    options: Sequence[str],
    vote: GradeResult | None = None,
) -> dict[str, Any]:
    payload = dict(row)
    grade = apply_gold(
        decision=str(row.get("decision") or ""),
        coverage=str(row.get("coverage_status") or "ok"),
        gold=gold,
        options=options,
        vote=vote,
    )
    payload["action_correct_heuristic"] = row.get("action_correct")
    _apply_grade(payload, grade)
    payload["gold_after_attempts"] = True
    payload["label_status"] = gold.label_status
    payload["annotator"] = gold.annotator
    return payload


def _apply_grade(payload: dict[str, Any], grade: PilotGrade) -> None:
    payload["action_correct"] = grade.action_correct
    payload["chosen_action"] = grade.chosen_action
    payload["appropriate_abstention"] = grade.appropriate_abstention
    payload["unacceptable_hit"] = grade.unacceptable_hit
    payload["ungraded"] = grade.ungraded
    payload["grade_status"] = grade.grade_status
    payload["grade_error"] = grade.grade_error
    payload["judge_picked"] = grade.judge_picked
    payload["committed"] = grade.committed
    payload["abstain"] = grade.abstain
    payload["grader_source"] = grade.source
    payload["grader_reason"] = grade.reason


def unique_arm_rows(results: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    picked: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in results:
        key = (str(row.get("case_id")), str(row.get("mode")))
        if key not in picked or row.get("match") == "request":
            picked[key] = row
    return list(picked.values())


def _row_cost_status(row: Mapping[str, Any]) -> str:
    token = str(row.get("cost_status") or "").strip().lower()
    if token in {COST_EXACT, COST_CACHED, "partial"}:
        return token
    requests = row.get("requests")
    hits = row.get("cache_hits")
    req = int(requests) if isinstance(requests, int) and not isinstance(requests, bool) else 0
    hit = int(hits) if isinstance(hits, int) and not isinstance(hits, bool) else 0
    return cost_status_for(req, hit)


def _exact_usd(row: Mapping[str, Any] | None) -> float | None:
    if row is None:
        return None
    if _row_cost_status(row) != COST_EXACT:
        return None
    usd = row.get("usd")
    if isinstance(usd, bool) or not isinstance(usd, (int, float)):
        return 0.0
    return float(usd)


def _credit(value: object) -> float:
    return 1.0 if value is True else 0.0


def paired_primary(scored: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_case: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
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
        ox_c = _credit(ox.get("action_correct"))
        sr_c = _credit(sr.get("action_correct"))
        kind = str(ox.get("kind") or "")
        ox_usd = _exact_usd(ox)
        sr_usd = _exact_usd(sr)
        maj = arms.get("majority")
        paired.append(
            {
                "case_id": case_id,
                "kind": kind,
                "oxford_correct": ox_c,
                "short_review_correct": sr_c,
                "delta": sr_c - ox_c,
                "oxford_usd": ox_usd,
                "short_review_usd": sr_usd,
                "majority_usd": _exact_usd(maj),
                "oxford_cached": _row_cost_status(ox) == COST_CACHED,
                "short_review_cached": _row_cost_status(sr) == COST_CACHED,
            }
        )
    n = len(paired)
    delta = sum(p["delta"] for p in paired) / n if n else None
    sr_exact = [float(p["short_review_usd"]) for p in paired if p["short_review_usd"] is not None]
    ox_exact = [float(p["oxford_usd"]) for p in paired if p["oxford_usd"] is not None]
    mean_sr = sum(sr_exact) / len(sr_exact) if sr_exact else None
    mean_ox = sum(ox_exact) / len(ox_exact) if ox_exact else None
    by_kind: dict[str, list[float]] = defaultdict(list)
    for row in paired:
        by_kind[row["kind"]].append(row["delta"])
    class_delta = {
        kind: {"n": len(vals), "delta": sum(vals) / len(vals)}
        for kind, vals in sorted(by_kind.items())
    }
    return {
        "n_paired": n,
        "delta": delta,
        "mean_usd_short_review": mean_sr,
        "mean_usd_oxford": mean_ox,
        "n_cached_oxford": sum(1 for p in paired if p["oxford_cached"]),
        "n_cached_short_review": sum(1 for p in paired if p["short_review_cached"]),
        "class_delta": class_delta,
        "pairs": paired,
    }


def decide_rule(primary: Mapping[str, Any], n_complete: int, bad_share: float) -> dict[str, Any]:
    """Правило из preregistration. Product default здесь не меняем."""
    if n_complete < 30 or bad_share > 0.20:
        return {
            "verdict": "inconclusive",
            "product_default": "debate",
            "reason": (
                f"rule1: n_complete={n_complete} < 30 or ungraded/failed share "
                f"{bad_share:.3f} > 0.20"
            ),
        }
    delta = primary.get("delta")
    mean_sr = primary.get("mean_usd_short_review")
    mean_ox = primary.get("mean_usd_oxford")
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


def coverage_stats(scored: Sequence[Mapping[str, Any]], n_cases: int) -> dict[str, Any]:
    by_case: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
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


def rescore_report(
    report: Mapping[str, Any],
    gold_map: Mapping[str, GoldRow],
    cases: Mapping[str, BenchmarkCase],
    *,
    votes: Mapping[tuple[str, str], GradeResult] | None = None,
    judge_name: str = "executable",
) -> dict[str, Any]:
    """Пересчитать action_correct по sidecar. Исходный dict не мутируем."""
    rows = unique_arm_rows(list(report.get("results") or []))
    scored: list[dict[str, Any]] = []
    for row in rows:
        case_id = str(row["case_id"])
        gold = gold_map[case_id]
        case = cases[case_id]
        key = (case_id, str(row["mode"]))
        vote = None if votes is None else votes.get(key)
        scored.append(overlay_row(row, gold, case.answer_options, vote=vote))
    cov = coverage_stats(scored, int(report.get("n_cases") or len(cases)))
    primary = paired_primary(scored)
    rule = decide_rule(primary, cov["n_complete"], cov["ungraded_failed_share"])
    out = dict(report)
    schema = str(report.get("schema") or "")
    out["schema"] = LIVE_G_V2 if schema == LIVE_G_V1 else schema or LIVE_G_V2
    out["rescored_from"] = schema or "unknown"
    out["judge"] = judge_name
    out["blind_llm_judge"] = judge_name == "llm"
    out["independent_validation"] = False
    out["coverage"] = cov
    out["primary"] = {k: v for k, v in primary.items() if k != "pairs"}
    out["pairs"] = primary["pairs"]
    out["decision_rule"] = rule
    out["results"] = scored
    return out
