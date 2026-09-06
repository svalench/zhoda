"""Адаптер ZhodaEngine → EngineOutcome для бенчмарка."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from zhoda_core.config import load_council_config, make_engine, make_provider
from zhoda_core.models import Protocol, Verdict

from .baselines import BestOfNArm, SelfConsistencyArm, SinglePassCouncilArm
from .datasets import SeedAgent, combine_case_context, supplied_positions_from_seeds
from .runner import (
    ALL_MODES,
    MODE_BEST_OF_N,
    MODE_COUNCIL,
    MODE_MAJORITY,
    MODE_SELF_CONSISTENCY,
    MODE_SHORT_REVIEW,
    MODE_ZHODA,
    DeliberationEngine,
    EngineOutcome,
    cost_kwargs,
)


def outcome_from_verdict(verdict: Verdict) -> EngineOutcome:
    """Verdict → плоский EngineOutcome. router_confidence ≠ answer confidence."""
    return EngineOutcome(
        decision=verdict.decision,
        minority_report=verdict.minority_report,
        switches=len(verdict.switches),
        rounds_taken=verdict.rounds_taken,
        confidence=None,
        router_confidence=verdict.router_confidence,
        dead_ends=len(verdict.paths_rejected),
        zhoda_reached=verdict.zhoda_reached,
        **cost_kwargs(verdict.cost),  # type: ignore[arg-type]
    )


def beneficial_switch_count(
    switches: Sequence[Any],
    *,
    gold: str,
    truth_keywords: Sequence[str] = (),
) -> int:
    """Переход к лучшему action (gold/truth), не любой switch."""
    n = 0
    for sw in switches:
        blob = " ".join(
            [
                str(getattr(sw, "convinced_by", "") or ""),
                str(getattr(sw, "quote_span", "") or ""),
                str(getattr(sw, "to_faction", "") or ""),
            ]
        ).casefold()
        if gold and gold.casefold() in blob:
            n += 1
        elif any(k.casefold() in blob for k in truth_keywords if k):
            n += 1
    return n


class ZhodaArm:
    """ZhodaEngine: debate, vote, или opt-in short_review."""

    def __init__(
        self,
        engine: Any,
        *,
        protocol: Protocol,
        clarify_mode: str = "no-clarify",
        expected_models: Sequence[str] | None = None,
        expected_rounds: int | None = None,
        spy: Any = None,
    ) -> None:
        self.engine = engine
        self.protocol = protocol
        self.clarify_mode = clarify_mode
        self.expected_models = tuple(expected_models) if expected_models is not None else None
        self.expected_rounds = expected_rounds
        self.spy = spy

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
        context: str = "",
    ) -> EngineOutcome:
        from .spec import SpecMismatch

        del n_samples, usd_budget, token_budget
        if self.expected_models is not None and tuple(models) != self.expected_models:
            raise SpecMismatch(
                f"adapter models {tuple(models)} != spec council {self.expected_models}"
            )
        engine_rounds = getattr(self.engine, "rounds_cap", None)
        if self.expected_rounds is not None:
            if rounds != self.expected_rounds:
                raise SpecMismatch(f"adapter rounds {rounds} != spec {self.expected_rounds}")
            if engine_rounds is not None and int(engine_rounds) != self.expected_rounds:
                raise SpecMismatch(
                    f"engine.rounds_cap {engine_rounds} != spec {self.expected_rounds}"
                )
        forced = supplied_positions_from_seeds(seed_agents, models)
        verdict = await self.engine.deliberate(
            question,
            force_protocol=self.protocol,
            clarify_mode=self.clarify_mode,
            context=combine_case_context(context, seed_agents),
            supplied_positions=forced or None,
        )
        if self.spy is not None:
            self.spy.verify()
        if isinstance(verdict, EngineOutcome):
            return verdict
        if isinstance(verdict, Verdict):
            outcome = outcome_from_verdict(verdict)
            gold = ""
            if answer_options:
                gold = answer_options[0]
            outcome.beneficial_switches = beneficial_switch_count(
                verdict.switches,
                gold=gold,
            )
            report = getattr(getattr(self.engine, "provider", None), "question_report", None)
            if callable(report):
                from .spy import usage_from_report

                outcome.engine_usage = usage_from_report(report(), role="engine")
            return outcome
        return EngineOutcome(decision=str(verdict))


def arm_cache_path(
    base: str | Path,
    mode: str,
    *,
    replicate_id: int = 0,
    cache_mode: str = "fresh",
) -> str:
    """`.zhoda-cache.db` + mode → `.zhoda-cache-zhoda.db`.

    Independent replicate — отдельный namespace. Exact-cache replay — тот же файл.
    """
    path = Path(base)
    extra = ""
    if cache_mode != "replay" and replicate_id:
        extra = f"-r{replicate_id}"
    return str(path.with_name(f"{path.stem}-{mode}{extra}{path.suffix}"))


def build_live_arms(
    config_path: str | Path,
    *,
    clarify_mode: str = "no-clarify",
    rounds_cap: int | None = None,
    cache_path: str | Path | None = None,
    transcripts_dir: str | Path | None = None,
    isolate_cache: bool = True,
    council: Sequence[str] | None = None,
    replicate_id: int = 0,
    cache_mode: str = "fresh",
    spies: dict[str, Any] | None = None,
    expected_models: Sequence[str] | None = None,
    modes: Sequence[str] | None = None,
) -> dict[str, DeliberationEngine]:
    """Собрать arms. Default = ALL_MODES (без short_review). Pilot передаёт PILOT_ARMS."""
    wanted = tuple(modes) if modes is not None else ALL_MODES
    cfg = dict(load_council_config(config_path))
    if council is not None:
        cfg["council"] = list(council)
    if cache_path is not None:
        cfg["cache_path"] = str(cache_path)
    base_cache = str(cfg.get("cache_path") or ".zhoda-cache.db")
    transcripts = None if transcripts_dir is None else str(transcripts_dir)
    roster = list(cfg["council"])
    chairman = str(cfg.get("chairman") or roster[0])
    judges = cfg["judges"]
    expect = tuple(expected_models) if expected_models is not None else tuple(roster)

    from .cache_guard import ensure_fresh_cache

    if isolate_cache:
        for mode in wanted:
            ensure_fresh_cache(
                arm_cache_path(
                    base_cache,
                    mode,
                    replicate_id=replicate_id,
                    cache_mode=cache_mode,
                ),
                cache_mode=cache_mode,
            )
    else:
        ensure_fresh_cache(base_cache, cache_mode=cache_mode)

    def provider_for(mode: str) -> Any:
        from .spy import SpyingProvider

        arm_cfg = dict(cfg)
        if isolate_cache:
            arm_cfg["cache_path"] = arm_cache_path(
                base_cache,
                mode,
                replicate_id=replicate_id,
                cache_mode=cache_mode,
            )
        provider = make_provider(arm_cfg)
        spy = (spies or {}).get(mode)
        if spy is not None:
            return SpyingProvider(provider, spy, role="engine")
        return provider

    shared_provider: Any = None

    def engine_provider(mode: str) -> Any:
        nonlocal shared_provider
        if isolate_cache:
            return provider_for(mode)
        if shared_provider is None:
            shared_provider = provider_for(mode)
        return shared_provider

    def zhoda_like(mode: str, protocol: Protocol) -> ZhodaArm:
        prov = engine_provider(mode)
        eng = make_engine(cfg, prov, transcripts_dir=transcripts, rounds_cap=rounds_cap)
        return ZhodaArm(
            eng,
            protocol=protocol,
            clarify_mode=clarify_mode,
            expected_models=expect,
            expected_rounds=rounds_cap,
            spy=(spies or {}).get(mode),
        )

    arms: dict[str, DeliberationEngine] = {}
    if MODE_ZHODA in wanted:
        arms[MODE_ZHODA] = zhoda_like(MODE_ZHODA, Protocol.DEBATE)
    if MODE_MAJORITY in wanted:
        arms[MODE_MAJORITY] = zhoda_like(MODE_MAJORITY, Protocol.VOTE)
    if MODE_SHORT_REVIEW in wanted:
        arms[MODE_SHORT_REVIEW] = zhoda_like(MODE_SHORT_REVIEW, Protocol.SHORT_REVIEW)
    if MODE_COUNCIL in wanted:
        arms[MODE_COUNCIL] = SinglePassCouncilArm(
            engine_provider(MODE_COUNCIL),
            chairman=chairman,
        )
    if MODE_SELF_CONSISTENCY in wanted:
        arms[MODE_SELF_CONSISTENCY] = SelfConsistencyArm(
            engine_provider(MODE_SELF_CONSISTENCY),
            judge_model=chairman,
        )
    if MODE_BEST_OF_N in wanted:
        arms[MODE_BEST_OF_N] = BestOfNArm(
            engine_provider(MODE_BEST_OF_N),
            judge_model=str(judges[0]),
        )
    return arms


def live_cache_paths(base: str | Path) -> dict[str, str]:
    """Пути sqlite по arm — для тестов и отчёта."""
    return {mode: arm_cache_path(base, mode) for mode in (*ALL_MODES, "judge")}
