"""Benchmark harness — exists from day one (critique §7), not after.

Judging protocol (critique round-3 §5 — the bench must survive HN comments):
- BLIND grading: the judge never sees which arm produced which answer
- HUMAN grading on a 20-30 task subset; LLM-judge is auxiliary only,
  with position-swap and length normalization (LLM judges prefer long,
  structured answers — which is exactly Zhoda's output shape, so an
  unblinded LLM judge would be biased in our favor)
- Dataset and rubrics are published in this directory (tasks.jsonl —
  seed set of 3, growing to 50-100)

Arms: single model / single-pass council (Karpathy-style) / Zhoda full /
Zhoda without Stage 0 / Zhoda without factions.
Metrics: rubric score, calibration, cost, latency.
"""

from pydantic import BaseModel


class BenchTask(BaseModel):
    question: str
    rubric: list[str]  # published grading criteria
    domain: str = "architecture"


class BenchResult(BaseModel):
    arm: str
    task: str
    score: float
    cost_usd: float
    latency_s: float


async def run_bench(tasks: list[BenchTask]) -> list[BenchResult]:
    raise NotImplementedError  # TODO(mvp): after first working deliberation
