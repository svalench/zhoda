"""Council YAML → provider + engine. Общая сборка для CLI и MCP."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from .elicitor import DEFAULT_MAX_ELICIT_TURNS
from .engine import ZhodaEngine
from .providers.openrouter import OpenRouterProvider


def load_council_config(path: str | Path) -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("zhoda.yaml must be a mapping")
    return data


def make_provider(
    cfg: dict[str, Any],
    *,
    budget_usd: float | None = None,
) -> OpenRouterProvider:
    budget = float(cfg.get("budget_per_question_usd", 0.0) if budget_usd is None else budget_usd)
    return OpenRouterProvider(
        budget_usd=budget,
        max_concurrency=int(cfg.get("max_concurrency", 4)),
        prices=cfg.get("prices"),
        cache_path=cfg.get("cache_path"),
    )


def make_engine(
    cfg: dict[str, Any],
    provider: OpenRouterProvider,
    *,
    transcripts_dir: str | None = None,
    rounds_cap: int | None = None,
) -> ZhodaEngine:
    judges = cfg.get("judges")
    if not judges:
        raise ValueError(
            "no judges configured — judges must sit outside the council; refusing to start"
        )
    classifiers = cfg.get("router_classifiers")
    if not classifiers or len(set(classifiers)) < 2:
        raise ValueError("router_classifiers: two distinct models, from config")
    council = cfg["council"]
    escalation = cfg.get("escalation", {}) or {}
    cap = int(cfg.get("rounds_cap", 4)) if rounds_cap is None else rounds_cap
    return ZhodaEngine(
        provider,
        council,
        chairman=cfg.get("chairman", council[0]),
        judges=tuple(judges),
        router_classifiers=tuple(classifiers),
        rounds_cap=cap,
        stability_rounds=int(cfg.get("stability_rounds", 2)),
        devils_advocate=bool(cfg.get("devils_advocate", True)),
        ambiguity_threshold=float(cfg.get("ambiguity_threshold", 0.6)),
        max_new_per_round=int(cfg.get("max_new_per_round", 3)),
        max_active=int(cfg.get("max_active", 6)),
        max_elicit_turns=int(cfg.get("max_elicit_turns", DEFAULT_MAX_ELICIT_TURNS)),
        escalation_model=(escalation.get("model") if escalation.get("enabled") else None),
        transcripts_dir=transcripts_dir or str(cfg.get("transcripts_dir", "transcripts")),
    )
