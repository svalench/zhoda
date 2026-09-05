"""Слепой LLM-судья: не видит имя arm, требует committed pick."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, Sequence

from zhoda_core.providers.openrouter import OpenRouterProvider, make_cache_key
from zhoda_core.stage_dtos import BlindGradeVote, parse_stage

from .datasets import KIND_XOR, BenchmarkCase

BLIND_JUDGE_PROMPT = """Score whether the system's answer COMMITS to the gold recommendation.

Question:
{question}

Gold recommendation: {gold}
Allowed labels: {options}

System answer (the producing system is hidden):
{decision}

Rules:
- committed=true only if the answer's ACTION is the gold recommendation.
- A labeled recommendation ("Recommended (majority at cap, not zhoda): <pick>")
  IS a committed pick. The Dissent: section after it is not a hedge.
- A dissent map that only lists faction theses under "No zhoda" without a
  recommended action is committed=false.
- "It depends", hedging, or adopting both options as the action is committed=false.
- Naming a non-gold option as the action: committed=false, picked=<that option>.
- picked MUST be exactly one allowed label (or empty). Do not negate a label.

ONLY JSON:
{{"committed": true, "picked": "<label or empty>", "reason": "<one sentence>"}}"""


class GradeStatus(StrEnum):
    GRADED = "graded"
    UNGRADED = "ungraded"


@dataclass(frozen=True)
class GradeResult:
    """Typed grader outcome. ungraded не равен incorrect."""

    status: GradeStatus
    committed: bool | None = None
    picked_id: str | None = None
    correct: bool | None = None
    reason: str = ""
    error: str | None = None


class BlindJudge(Protocol):
    async def score(self, case: BenchmarkCase, decision: str) -> GradeResult: ...


def gold_label(case: BenchmarkCase) -> str:
    """XOR: первый answer_option. Иначе — первое предложение ground_truth."""
    if case.kind == KIND_XOR and case.answer_options:
        return case.answer_options[0]
    first = case.ground_truth.split(".")[0].strip()
    return first or case.ground_truth[:80]


def _fold(text: str) -> str:
    folded = text.strip().casefold()
    stripped = re.sub(r"[^\w\s]+", " ", folded, flags=re.UNICODE)
    return " ".join(stripped.split())


def resolve_picked_id(picked: str, allowed: Sequence[str]) -> str | None:
    """Точный ID из allowed (после fold). Без substring-угадывания."""
    needle = _fold(picked)
    if not needle or not allowed:
        return None
    hits = [opt for opt in allowed if _fold(opt) == needle]
    if len(hits) == 1:
        return hits[0]
    return None


def pick_matches_gold(
    picked: str,
    gold: str,
    allowed: Sequence[str] = (),
) -> bool:
    """Exact allowed ID, не 'PostgreSQL' ⊂ 'Not PostgreSQL'."""
    labels = tuple(allowed) if allowed else (gold,)
    picked_id = resolve_picked_id(picked, labels)
    if picked_id is None:
        return False
    return _fold(picked_id) == _fold(gold)


def apply_blind_verdict(
    committed: bool,
    picked: str,
    gold: str,
    allowed: Sequence[str] = (),
) -> bool:
    """Зачёт только committed pick в сторону gold по exact ID."""
    return committed is True and pick_matches_gold(picked, gold, allowed)


def grade_blind_vote(
    vote: BlindGradeVote,
    *,
    gold: str,
    allowed: Sequence[str],
) -> GradeResult:
    labels = tuple(allowed) if allowed else (gold,)
    picked_raw = vote.picked.strip()
    picked_id = resolve_picked_id(picked_raw, labels) if picked_raw else None
    if vote.committed:
        if not picked_raw:
            return GradeResult(
                status=GradeStatus.UNGRADED,
                committed=True,
                picked_id=None,
                error="missing_pick",
                reason=vote.reason,
            )
        if picked_id is None:
            return GradeResult(
                status=GradeStatus.UNGRADED,
                committed=True,
                picked_id=None,
                error="unknown_pick",
                reason=vote.reason,
            )
        return GradeResult(
            status=GradeStatus.GRADED,
            committed=True,
            picked_id=picked_id,
            correct=_fold(picked_id) == _fold(gold),
            reason=vote.reason,
        )
    return GradeResult(
        status=GradeStatus.GRADED,
        committed=False,
        picked_id=picked_id,
        correct=False,
        reason=vote.reason,
    )


class BlindLlmJudge:
    """Один ask_json на decision. Arm name в промпт не попадает."""

    def __init__(self, provider: OpenRouterProvider, model: str) -> None:
        self.provider = provider
        self.model = model

    async def score(self, case: BenchmarkCase, decision: str) -> GradeResult:
        gold = gold_label(case)
        allowed = case.answer_options or (gold,)
        options = ", ".join(allowed)
        prompt = BLIND_JUDGE_PROMPT.format(
            question=case.question,
            gold=gold,
            options=options,
            decision=decision,
        )
        obj = await self.provider.ask_json(
            self.model,
            prompt,
            cache_key=make_cache_key("bench-judge", case.id, decision),
        )
        parsed = parse_stage(BlindGradeVote, obj, stage="blind_judge", prompt=prompt)
        if parsed.value is None:
            error = parsed.error.error if parsed.error else "invalid_grade"
            return GradeResult(
                status=GradeStatus.UNGRADED,
                error=error,
                reason=parsed.error.raw_preview if parsed.error else "",
            )
        return grade_blind_vote(parsed.value, gold=gold, allowed=allowed)
