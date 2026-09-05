"""Benchmark suite for Zhoda robustness evaluation.

Suites:
- ``sycophancy``: biased-premise and bandwagon cases measuring whether
  deliberation resists false user premises and weak injected majorities.
- ``minority``: true-minority traps measuring whether a lone correct
  position is preserved in the minority report and can convince others.
- ``decision``: 51 XOR architecture / security / ops tasks plus the seed
  suites. Headline metric is keyword accuracy with foil_keywords so a
  dissent map naming both options does not count as a pick.

Comparison is five-way: Zhoda debate vs majority (vote, no debate) vs
single-pass council vs compute-matched self-consistency vs best-of-N.
"""

from .datasets import BenchmarkCase, SeedAgent, builtin_cases, dump_cases, load_cases
from .metrics import (
    brier_score,
    convincing_power,
    dead_ends_per_usd,
    minority_preservation_rate,
    resistance_rate,
    summarize,
    summarize_tables,
    sycophancy_flip_rate,
)
from .runner import (
    ALL_MODES,
    MODE_BEST_OF_N,
    MODE_COUNCIL,
    MODE_MAJORITY,
    MODE_SELF_CONSISTENCY,
    MODE_ZHODA,
    CaseResult,
    ComparativeRunner,
    DeliberationEngine,
    EngineOutcome,
    HeuristicJudge,
    MockEngine,
    ModelClient,
)

__all__ = [
    "ALL_MODES",
    "MODE_BEST_OF_N",
    "MODE_COUNCIL",
    "MODE_MAJORITY",
    "MODE_SELF_CONSISTENCY",
    "MODE_ZHODA",
    "BenchmarkCase",
    "SeedAgent",
    "builtin_cases",
    "dump_cases",
    "load_cases",
    "resistance_rate",
    "sycophancy_flip_rate",
    "minority_preservation_rate",
    "convincing_power",
    "dead_ends_per_usd",
    "brier_score",
    "summarize",
    "summarize_tables",
    "CaseResult",
    "ComparativeRunner",
    "DeliberationEngine",
    "EngineOutcome",
    "HeuristicJudge",
    "MockEngine",
    "ModelClient",
]
