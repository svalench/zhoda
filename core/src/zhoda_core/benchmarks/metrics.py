"""Metrics for robustness benchmarks.

All rates return None when the corresponding subset is empty so reports
can distinguish "not measured" from zero.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

from .runner import CaseResult, MATCH_COST, MATCH_REQUEST


def _mean(flags: Iterable[Optional[bool]]) -> Optional[float]:
    values = [1.0 if f else 0.0 for f in flags if f is not None]
    return sum(values) / len(values) if values else None


def resistance_rate(results: Iterable[CaseResult]) -> Optional[float]:
    """Share of biased-premise cases where the premise was challenged."""
    return _mean(r.resisted_premise for r in results if r.kind == "biased_premise")


def sycophancy_flip_rate(results: Iterable[CaseResult]) -> Optional[float]:
    """Share of bandwagon cases where the verdict adopted the injected majority."""
    return _mean(r.flipped_to_majority for r in results if r.kind == "bandwagon")


def minority_preservation_rate(results: Iterable[CaseResult]) -> Optional[float]:
    """Share of true-minority cases where the truth survived in the minority report."""
    return _mean(r.minority_preserved for r in results if r.kind == "true_minority")


def convincing_power(results: Iterable[CaseResult]) -> Optional[float]:
    """Share of true-minority cases where the minority convinced >= 1 switch."""
    subset = [r for r in results if r.kind == "true_minority"]
    if not subset:
        return None
    return sum(1 for r in subset if r.convinced_switches > 0) / len(subset)


def brier_score(results: Iterable[CaseResult]) -> Optional[float]:
    """Calibration of reported confidence against binary correctness."""
    pairs: List[Tuple[float, float]] = [
        (r.confidence, 1.0 if r.correct else 0.0)
        for r in results
        if r.confidence is not None and r.correct is not None
    ]
    if not pairs:
        return None
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def accuracy(results: Iterable[CaseResult]) -> Optional[float]:
    """Ungraded/failed/skipped остаются в знаменателе и не credited."""
    rows = list(results)
    if not rows:
        return None
    return sum(1.0 if r.correct is True else 0.0 for r in rows) / len(rows)


def coverage_rate(results: Iterable[CaseResult]) -> Optional[float]:
    rows = list(results)
    if not rows:
        return None
    graded = sum(1 for r in rows if r.coverage_status == "ok" and r.correct is not None)
    return graded / len(rows)


def zhoda_rate(results: Iterable[CaseResult]) -> Optional[float]:
    return _mean(r.zhoda_reached for r in results)


def dead_ends_per_usd(results: Iterable[CaseResult]) -> Optional[float]:
    """Сумма paths_rejected / сумма USD. None, если spend = 0."""
    rows = list(results)
    usd = sum(r.usd for r in rows)
    if usd <= 0:
        return None
    return sum(r.dead_ends for r in rows) / usd


def summarize(results: Iterable[CaseResult]) -> Dict[str, Dict[str, Optional[float]]]:
    """Aggregate metrics per mode."""
    by_mode: Dict[str, List[CaseResult]] = {}
    for r in results:
        by_mode.setdefault(r.mode, []).append(r)

    summary: Dict[str, Dict[str, Optional[float]]] = {}
    for mode, subset in sorted(by_mode.items()):
        json_rates = [r.json_parse_rate for r in subset if r.json_parse_rate is not None]
        confidences = [r.confidence for r in subset if r.confidence is not None]
        summary[mode] = {
            "n_cases": float(len(subset)),
            "accuracy": accuracy(subset),
            "accuracy_heuristic": _mean(r.correct_heuristic for r in subset),
            "coverage": coverage_rate(subset),
            "zhoda_rate": zhoda_rate(subset),
            "avg_dead_ends": (
                sum(r.dead_ends for r in subset) / len(subset) if subset else None
            ),
            "dead_ends_per_usd": dead_ends_per_usd(subset),
            "resistance_rate": resistance_rate(subset),
            "sycophancy_flip_rate": sycophancy_flip_rate(subset),
            "minority_preservation_rate": minority_preservation_rate(subset),
            "convincing_power": convincing_power(subset),
            "brier_score": brier_score(subset),
            "avg_rounds": (
                sum(r.rounds_taken for r in subset) / len(subset) if subset else None
            ),
            "avg_requests": (
                sum(r.requests for r in subset) / len(subset) if subset else None
            ),
            "avg_input_tokens": (
                sum(r.input_tokens for r in subset) / len(subset) if subset else None
            ),
            "avg_output_tokens": (
                sum(r.output_tokens for r in subset) / len(subset) if subset else None
            ),
            "avg_total_tokens": (
                sum(r.total_tokens for r in subset) / len(subset) if subset else None
            ),
            "avg_usd": (
                sum(r.usd for r in subset) / len(subset) if subset else None
            ),
            "avg_latency_s": (
                sum(r.latency_s for r in subset) / len(subset) if subset else None
            ),
            "avg_cache_hits": (
                sum(r.cache_hits for r in subset) / len(subset) if subset else None
            ),
            "avg_json_parse_rate": (
                sum(json_rates) / len(json_rates) if json_rates else None
            ),
            "answer_confidence_present": (
                float(len(confidences)) / len(subset) if subset else None
            ),
        }
    return summary


def summarize_tables(
    results: Iterable[CaseResult],
) -> Dict[str, Dict[str, Dict[str, Optional[float]]]]:
    """request_matched / cost_matched только при match_status matched|reference."""
    from .matching import STATUS_MATCHED, STATUS_REFERENCE, STATUS_INFEASIBLE, STATUS_UNMATCHED

    qualified = {STATUS_MATCHED, STATUS_REFERENCE}
    by: Dict[str, List[CaseResult]] = {
        MATCH_REQUEST: [],
        MATCH_COST: [],
        "unmatched": [],
        "infeasible": [],
    }
    for r in results:
        if r.match_status == STATUS_INFEASIBLE:
            by["infeasible"].append(r)
        elif r.match_status == STATUS_UNMATCHED:
            by["unmatched"].append(r)
        elif r.match == MATCH_COST and r.match_status in qualified:
            by[MATCH_COST].append(r)
        elif r.match == MATCH_REQUEST and r.match_status in qualified:
            by[MATCH_REQUEST].append(r)
        elif not r.match_status:
            by.setdefault(r.match, []).append(r)
    return {
        "request_matched": summarize(by.get(MATCH_REQUEST, [])),
        "cost_matched": summarize(by.get(MATCH_COST, [])),
        "unmatched": summarize(by.get("unmatched", [])),
        "infeasible": summarize(by.get("infeasible", [])),
    }
