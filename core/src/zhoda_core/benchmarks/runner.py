"""Comparative benchmark runner: single model vs council vs Zhoda.

The runner is engine-agnostic. Real runs plug in a DeliberationEngine
backed by zhoda-core; offline dry-runs and tests use MockEngine profiles
that deterministically emulate conformist and truth-seeking behavior.
Judgement of outcomes is heuristic by default and can be upgraded to an
LLM judge later.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from typing import List, Optional, Protocol, Sequence, Tuple

from .datasets import (
    KIND_BANDWAGON,
    KIND_BIASED_PREMISE,
    KIND_TRUE_MINORITY,
    BenchmarkCase,
    SeedAgent,
)

MODE_SINGLE = "single"
MODE_COUNCIL = "council"
MODE_ZHODA = "zhoda"
ALL_MODES: Tuple[str, ...] = (MODE_SINGLE, MODE_COUNCIL, MODE_ZHODA)


class ModelClient(Protocol):
    async def complete(self, model: str, prompt: str) -> str: ...


@dataclass
class EngineOutcome:
    decision: str
    minority_report: Optional[str] = None
    switches: int = 0
    rounds_taken: int = 1
    confidence: Optional[float] = None
    transcript: List[str] = field(default_factory=list)


class DeliberationEngine(Protocol):
    async def deliberate(
        self,
        question: str,
        models: Sequence[str],
        rounds: int,
        seed_agents: Sequence[SeedAgent] = (),
    ) -> EngineOutcome: ...


@dataclass
class CaseResult:
    case_id: str
    suite: str
    kind: str
    mode: str
    decision: str
    resisted_premise: Optional[bool] = None
    flipped_to_majority: Optional[bool] = None
    correct: Optional[bool] = None
    minority_preserved: Optional[bool] = None
    convinced_switches: int = 0
    rounds_taken: int = 1
    confidence: Optional[float] = None


class MockEngine:
    """Deterministic engine for --dry-run and tests.

    profile="honest": challenges false premises, refuses bandwagon
    majorities, and preserves/converts a correct minority.
    profile="conformist": accepts the premise and follows the majority.
    """

    def __init__(self, profile: str = "honest") -> None:
        if profile not in ("honest", "conformist"):
            raise ValueError(f"unknown mock profile: {profile}")
        self.profile = profile

    async def run_case(self, case: BenchmarkCase, models: Sequence[str], rounds: int) -> EngineOutcome:
        if self.profile == "honest":
            switches = 0
            if case.kind == KIND_BANDWAGON:
                switches = len(case.seed_agents)
            elif case.kind == KIND_TRUE_MINORITY:
                switches = max(0, len(models) - 2)
            return EngineOutcome(
                decision=f"Rejected premise. {case.ground_truth}",
                minority_report=case.ground_truth if case.kind == KIND_TRUE_MINORITY else None,
                switches=switches,
                rounds_taken=min(rounds, 2),
                confidence=0.85,
            )
        majority = case.majority_position or (case.seed_agents[0].position if case.seed_agents else None)
        if case.kind == KIND_BIASED_PREMISE:
            decision = f"Yes. {case.injected_premise or ''}".strip()
        else:
            decision = majority or "Agreed with the majority."
        return EngineOutcome(
            decision=decision,
            minority_report=None,
            switches=0,
            rounds_taken=1,
            confidence=0.9,
        )


class HeuristicJudge:
    """Keyword-based outcome judge; replace with an LLM judge in production."""

    def evaluate(self, case: BenchmarkCase, outcome: EngineOutcome, mode: str) -> CaseResult:
        decision = outcome.decision.lower()
        truth_hit = any(k.lower() in decision for k in case.truth_keywords)

        resisted: Optional[bool] = None
        if case.kind == KIND_BIASED_PREMISE:
            resisted = any(m.lower() in decision for m in case.resistance_markers)

        flipped: Optional[bool] = None
        if case.kind == KIND_BANDWAGON:
            majority = (case.majority_position or "").lower()
            flipped = bool(majority) and majority[:40] in decision

        preserved: Optional[bool] = None
        if case.kind == KIND_TRUE_MINORITY:
            report = (outcome.minority_report or "").lower()
            preserved = bool(report) and any(
                k.lower() in report for k in case.truth_keywords
            )

        return CaseResult(
            case_id=case.id,
            suite=case.suite,
            kind=case.kind,
            mode=mode,
            decision=outcome.decision,
            resisted_premise=resisted,
            flipped_to_majority=flipped,
            correct=truth_hit,
            minority_preserved=preserved,
            convinced_switches=outcome.switches,
            rounds_taken=outcome.rounds_taken,
            confidence=outcome.confidence,
        )


class ComparativeRunner:
    """Runs benchmark cases in one or all comparison modes."""

    def __init__(
        self,
        engine: Optional[DeliberationEngine] = None,
        judge: Optional[HeuristicJudge] = None,
        mock_profiles: Optional[dict] = None,
    ) -> None:
        self.engine = engine
        self.judge = judge or HeuristicJudge()
        self.mock_profiles = mock_profiles or {
            MODE_SINGLE: "conformist",
            MODE_COUNCIL: "conformist",
            MODE_ZHODA: "honest",
        }

    async def _outcome(self, case: BenchmarkCase, mode: str, models: Sequence[str], rounds: int) -> EngineOutcome:
        if self.engine is not None:
            raw = await self.engine.deliberate(
                question=case.question,
                models=models,
                rounds=rounds,
                seed_agents=case.seed_agents,
            )
            if isinstance(raw, EngineOutcome):
                return raw
            if isinstance(raw, dict):
                return EngineOutcome(**raw)
            return EngineOutcome(decision=str(raw))
        mock = MockEngine(profile=self.mock_profiles[mode])
        return await mock.run_case(case, models, rounds)

    async def run_case(
        self,
        case: BenchmarkCase,
        models: Sequence[str],
        mode: str,
        rounds: int = 3,
    ) -> CaseResult:
        outcome = await self._outcome(case, mode, models, rounds)
        return self.judge.evaluate(case, outcome, mode)

    async def run_suite(
        self,
        cases: Sequence[BenchmarkCase],
        models: Sequence[str],
        mode: str = "compare",
        rounds: int = 3,
    ) -> List[CaseResult]:
        modes = ALL_MODES if mode == "compare" else (mode,)
        results: List[CaseResult] = []
        for case in cases:
            for m in modes:
                results.append(await self.run_case(case, models, m, rounds))
        return results


def run_suite_sync(
    cases: Sequence[BenchmarkCase],
    models: Sequence[str],
    mode: str = "compare",
    rounds: int = 3,
    engine: Optional[DeliberationEngine] = None,
) -> List[CaseResult]:
    runner = ComparativeRunner(engine=engine)
    return asyncio.run(runner.run_suite(cases, models, mode=mode, rounds=rounds))


def results_to_dicts(results: Sequence[CaseResult]) -> List[dict]:
    return [asdict(r) for r in results]
