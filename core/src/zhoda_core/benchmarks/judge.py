"""Слепой LLM-судья: не видит имя arm, требует committed pick."""

from __future__ import annotations

import re
from typing import Protocol

from zhoda_core.providers.openrouter import OpenRouterProvider, make_cache_key

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

ONLY JSON:
{{"committed": true, "picked": "<label or empty>", "reason": "<one sentence>"}}"""


class BlindJudge(Protocol):
    async def score(self, case: BenchmarkCase, decision: str) -> tuple[bool, str]: ...


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


def pick_matches_gold(picked: str, gold: str) -> bool:
    a = _fold(picked)
    b = _fold(gold)
    if not a or not b:
        return False
    return a == b or a in b or b in a


def apply_blind_verdict(committed: bool, picked: str, gold: str) -> bool:
    """Зачёт только committed pick в сторону gold."""
    return bool(committed) and pick_matches_gold(picked, gold)


class BlindLlmJudge:
    """Один ask_json на decision. Arm name в промпт не попадает."""

    def __init__(self, provider: OpenRouterProvider, model: str) -> None:
        self.provider = provider
        self.model = model

    async def score(self, case: BenchmarkCase, decision: str) -> tuple[bool, str]:
        gold = gold_label(case)
        options = ", ".join(case.answer_options) if case.answer_options else gold
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
        committed = bool(obj.get("committed"))
        picked = str(obj.get("picked") or "").strip()
        return apply_blind_verdict(committed, picked, gold), picked
