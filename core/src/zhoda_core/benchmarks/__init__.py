"""Benchmark suite for Zhoda robustness evaluation.

Two suites:
- ``sycophancy``: biased-premise and bandwagon cases measuring whether
  deliberation resists false user premises and weak injected majorities.
- ``minority``: true-minority traps measuring whether a lone correct
  position is preserved in the minority report and can convince others.

Comparison is always three-way: single model vs single-pass council vs
full Zhoda deliberation.
"""

from .datasets import BenchmarkCase, SeedAgent, builtin_cases, dump_cases, load_cases
from .metrics import (
    brier_score,
    convincing_power,
    minority_preservation_rate,
    resistance_rate,
    summarize,
    sycophancy_flip_rate,
)
from .runner import (
    CaseResult,
    ComparativeRunner,
    DeliberationEngine,
    EngineOutcome,
    HeuristicJudge,
    MockEngine,
    ModelClient,
)

__all__ = [
    "BenchmarkCase",
    "SeedAgent",
    "builtin_cases",
    "dump_cases",
    "load_cases",
    "resistance_rate",
    "sycophancy_flip_rate",
    "minority_preservation_rate",
    "convincing_power",
    "brier_score",
    "summarize",
    "CaseResult",
    "ComparativeRunner",
    "DeliberationEngine",
    "EngineOutcome",
    "HeuristicJudge",
    "MockEngine",
    "ModelClient",
]
