"""Эвристика цены/времени ДО запуска. Не LLM — считаем по составу совета."""

from __future__ import annotations

from typing import Any

from zhoda_core.providers.openrouter import DEFAULT_MAX_TOKENS

# Документированный коридор живого дебата (docs/05-live-run.md).
_DEBATE_REQUESTS_MIN = 15


def estimate_cost(cfg: dict[str, Any], protocol: str | None = None) -> dict[str, Any]:
    """Нижняя/верхняя оценка запросов, USD и latency. confirm_required всегда True."""
    council = list(cfg["council"])
    n = len(council)
    rounds = int(cfg.get("rounds_cap", 4))
    prices = cfg.get("prices") or {}
    budget = float(cfg.get("budget_per_question_usd", 0.0))
    proto = protocol or "debate"

    if proto == "vote":
        req_min = 2 + n + n + 3
        req_max = req_min + n
        latency_min, latency_max = 20, 90
    elif proto == "red_team":
        req_min = 15
        req_max = 30
        latency_min, latency_max = 40, 180
    else:
        req_min = _DEBATE_REQUESTS_MIN
        req_max = 10 + n * 4 + rounds * (n + 8) + 4
        latency_min, latency_max = 60, 90 * rounds

    priced = [float(prices[m]) for m in council if m in prices]
    avg_price = sum(priced) / len(priced) if priced else 0.0
    usd_max = round(req_max * avg_price * (DEFAULT_MAX_TOKENS / 1000), 4)
    note = (
        "budget 0 — :free models only"
        if budget == 0
        else f"hard cap ${budget} per question"
    )
    return {
        "protocol": proto,
        "requests_min": req_min,
        "requests_max": req_max,
        "usd_max": usd_max,
        "budget_usd": budget,
        "latency_s_min": latency_min,
        "latency_s_max": latency_max,
        "confirm_required": True,
        "note": note,
    }
