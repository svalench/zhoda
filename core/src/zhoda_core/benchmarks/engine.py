"""Адаптер ZhodaEngine → EngineOutcome для бенчмарка."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from zhoda_core.config import load_council_config, make_engine, make_provider
from zhoda_core.models import Protocol, Verdict

from .baselines import BestOfNArm, SelfConsistencyArm, SinglePassCouncilArm
from .datasets import SeedAgent, seed_agents_context
from .runner import (
    ALL_MODES,
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
        dead_ends=len(verdict.paths_rejected),
        zhoda_reached=verdict.zhoda_reached,
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


def arm_cache_path(base: str | Path, mode: str) -> str:
    """`.zhoda-cache.db` + mode → `.zhoda-cache-zhoda.db` (изолированный sqlite)."""
    path = Path(base)
    return str(path.with_name(f"{path.stem}-{mode}{path.suffix}"))


def build_live_arms(
    config_path: str | Path,
    *,
    clarify_mode: str = "no-clarify",
    rounds_cap: int | None = None,
    cache_path: str | Path | None = None,
    transcripts_dir: str | Path | None = None,
    isolate_cache: bool = True,
) -> dict[str, DeliberationEngine]:
    """Собрать 5 arms. По умолчанию у каждого свой sqlite-кэш — иначе vote
    после debate читает чужие позиции."""
    cfg = load_council_config(config_path)
    if cache_path is not None:
        cfg["cache_path"] = str(cache_path)
    base_cache = str(cfg.get("cache_path") or ".zhoda-cache.db")
    transcripts = None if transcripts_dir is None else str(transcripts_dir)
    council = cfg["council"]
    chairman = str(cfg.get("chairman") or council[0])
    judges = cfg["judges"]

    def provider_for(mode: str) -> Any:
        arm_cfg = dict(cfg)
        if isolate_cache:
            arm_cfg["cache_path"] = arm_cache_path(base_cache, mode)
        return make_provider(arm_cfg)

    zhoda_provider = provider_for(MODE_ZHODA)
    majority_provider = provider_for(MODE_MAJORITY) if isolate_cache else zhoda_provider
    zhoda_engine = make_engine(
        cfg, zhoda_provider, transcripts_dir=transcripts, rounds_cap=rounds_cap,
    )
    majority_engine = (
        make_engine(cfg, majority_provider, transcripts_dir=transcripts, rounds_cap=rounds_cap)
        if isolate_cache
        else zhoda_engine
    )
    council_provider = provider_for(MODE_COUNCIL) if isolate_cache else zhoda_provider
    sc_provider = provider_for(MODE_SELF_CONSISTENCY) if isolate_cache else zhoda_provider
    bon_provider = provider_for(MODE_BEST_OF_N) if isolate_cache else zhoda_provider
    return {
        MODE_ZHODA: ZhodaArm(
            zhoda_engine, protocol=Protocol.DEBATE, clarify_mode=clarify_mode,
        ),
        MODE_MAJORITY: ZhodaArm(
            majority_engine, protocol=Protocol.VOTE, clarify_mode=clarify_mode,
        ),
        MODE_COUNCIL: SinglePassCouncilArm(council_provider, chairman=chairman),
        MODE_SELF_CONSISTENCY: SelfConsistencyArm(sc_provider, judge_model=chairman),
        MODE_BEST_OF_N: BestOfNArm(bon_provider, judge_model=str(judges[0])),
    }


def live_cache_paths(base: str | Path) -> dict[str, str]:
    """Пути sqlite по arm — для тестов и отчёта."""
    return {mode: arm_cache_path(base, mode) for mode in (*ALL_MODES, "judge")}
