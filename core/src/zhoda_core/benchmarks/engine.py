"""Адаптер ZhodaEngine → EngineOutcome для бенчмарка."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from zhoda_core.config import load_council_config, make_engine, make_provider
from zhoda_core.models import Protocol, Verdict

from .baselines import BestOfNArm, SelfConsistencyArm, SinglePassCouncilArm
from .datasets import SeedAgent, seed_agents_context
from .runner import (
    MODE_BEST_OF_N,
    MODE_COUNCIL,
    MODE_MAJORITY,
    MODE_SELF_CONSISTENCY,
    MODE_ZHODA,
    DeliberationEngine,
    EngineOutcome,
    cost_kwargs,
)


def outcome_from_verdict(verdict: Verdict) -> EngineOutcome:
    """Verdict → плоский EngineOutcome для HeuristicJudge."""
    return EngineOutcome(
        decision=verdict.decision,
        minority_report=verdict.minority_report,
        switches=len(verdict.switches),
        rounds_taken=verdict.rounds_taken,
        confidence=verdict.router_confidence,
        **cost_kwargs(verdict.cost),  # type: ignore[arg-type]
    )


class ZhodaArm:
    """Настоящий движок: debate или vote (majority без раундов)."""

    def __init__(
        self,
        engine: Any,
        *,
        protocol: Protocol,
        clarify_mode: str = "no-clarify",
    ) -> None:
        self.engine = engine
        self.protocol = protocol
        self.clarify_mode = clarify_mode

    async def deliberate(
        self,
        question: str,
        models: Sequence[str],
        rounds: int,
        seed_agents: Sequence[SeedAgent] = (),
        *,
        n_samples: int | None = None,
        usd_budget: float | None = None,
        token_budget: int | None = None,
        answer_options: Sequence[str] = (),
    ) -> EngineOutcome:
        del models, rounds, n_samples, usd_budget, token_budget, answer_options
        verdict = await self.engine.deliberate(
            question,
            force_protocol=self.protocol,
            clarify_mode=self.clarify_mode,
            context=seed_agents_context(seed_agents),
        )
        if isinstance(verdict, EngineOutcome):
            return verdict
        if isinstance(verdict, Verdict):
            return outcome_from_verdict(verdict)
        return EngineOutcome(decision=str(verdict))


def build_live_arms(
    config_path: str | Path,
    *,
    clarify_mode: str = "no-clarify",
) -> dict[str, DeliberationEngine]:
    """Собрать 5 arms с одним OpenRouterProvider из YAML."""
    cfg = load_council_config(config_path)
    provider = make_provider(cfg)
    engine = make_engine(cfg, provider)
    council = cfg["council"]
    chairman = str(cfg.get("chairman") or council[0])
    judges = cfg["judges"]
    return {
        MODE_ZHODA: ZhodaArm(
            engine, protocol=Protocol.DEBATE, clarify_mode=clarify_mode,
        ),
        MODE_MAJORITY: ZhodaArm(
            engine, protocol=Protocol.VOTE, clarify_mode=clarify_mode,
        ),
        MODE_COUNCIL: SinglePassCouncilArm(provider, chairman=chairman),
        MODE_SELF_CONSISTENCY: SelfConsistencyArm(provider, judge_model=chairman),
        MODE_BEST_OF_N: BestOfNArm(provider, judge_model=str(judges[0])),
    }
