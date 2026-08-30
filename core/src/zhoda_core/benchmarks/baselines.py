"""Compute-matched бейзлайны: self-consistency, best-of-N, single-pass council.

Не ZhodaEngine.debate: отдельные промпты через тот же OpenRouterProvider.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Sequence

from zhoda_core.providers.openrouter import OpenRouterProvider, make_cache_key

from .datasets import SeedAgent, seed_agents_context
from .runner import EngineOutcome

ANSWER_PROMPT = """Answer the question independently. Be concise.

Question: {question}
{context}

Respond with the decision first, then a short justification."""

SYNTHESIZE_PROMPT = """You chair a single-pass council. Synthesize ONE decision from the
independent answers. Do not invent a new option the answers did not consider.

Question: {question}

Answers:
{answers}

Respond with the synthesized decision only."""

PICK_BEST_PROMPT = """Question: {question}

Candidates:
{candidates}

Pick the single best candidate. ONLY valid JSON: {{"index": 1}}
Index is 1-based."""


def _majority_text(answers: Sequence[str]) -> str:
    """Голос по нормализованному тексту; ничья → первый ответ."""
    if not answers:
        return ""
    normalized = [a.strip() for a in answers if a.strip()]
    if not normalized:
        return answers[0]
    counts = Counter(normalized)
    winner, _ = counts.most_common(1)[0]
    return winner


class SelfConsistencyArm:
    """N сэмплов одной модели, majority по тексту ответа. N = compute budget."""

    def __init__(self, provider: OpenRouterProvider) -> None:
        self.provider = provider

    async def deliberate(
        self,
        question: str,
        models: Sequence[str],
        rounds: int,
        seed_agents: Sequence[SeedAgent] = (),
        *,
        n_samples: int | None = None,
    ) -> EngineOutcome:
        n = max(n_samples or rounds, 1)
        model = models[0]
        ctx = seed_agents_context(seed_agents)
        prompt = ANSWER_PROMPT.format(question=question, context=ctx)
        self.provider.begin_question()
        answers = await asyncio.gather(
            *(self.provider.complete(model, prompt) for _ in range(n))
        )
        report = self.provider.question_report()
        return EngineOutcome(
            decision=_majority_text(answers),
            rounds_taken=1,
            requests=report.requests,
            transcript=list(answers),
        )


class BestOfNArm:
    """N сэмплов одной модели + judge выбирает лучший. N+1 вызовов."""

    def __init__(self, provider: OpenRouterProvider, judge_model: str) -> None:
        self.provider = provider
        self.judge_model = judge_model

    async def deliberate(
        self,
        question: str,
        models: Sequence[str],
        rounds: int,
        seed_agents: Sequence[SeedAgent] = (),
        *,
        n_samples: int | None = None,
    ) -> EngineOutcome:
        n = max(n_samples or rounds, 1)
        model = models[0]
        ctx = seed_agents_context(seed_agents)
        prompt = ANSWER_PROMPT.format(question=question, context=ctx)
        self.provider.begin_question()
        answers = await asyncio.gather(
            *(self.provider.complete(model, prompt) for _ in range(n))
        )
        numbered = "\n".join(f"{i}. {a}" for i, a in enumerate(answers, start=1))
        pick = await self.provider.ask_json(
            self.judge_model,
            PICK_BEST_PROMPT.format(question=question, candidates=numbered),
            cache_key=make_cache_key("bon", question, numbered),
        )
        index = int(pick.get("index") or 1)
        chosen = answers[max(0, min(index, len(answers)) - 1)]
        report = self.provider.question_report()
        return EngineOutcome(
            decision=chosen,
            rounds_taken=1,
            requests=report.requests,
            transcript=list(answers),
        )


class SinglePassCouncilArm:
    """Каждая модель — один ответ, chairman синтезирует (Karpathy, без фракций).

    Если n_samples > |models|+1, лишние слоты — доп. сэмплы первой модели.
    """

    def __init__(self, provider: OpenRouterProvider, chairman: str) -> None:
        self.provider = provider
        self.chairman = chairman

    async def deliberate(
        self,
        question: str,
        models: Sequence[str],
        rounds: int,
        seed_agents: Sequence[SeedAgent] = (),
        *,
        n_samples: int | None = None,
    ) -> EngineOutcome:
        n = n_samples or (len(models) + 1)
        del rounds
        ctx = seed_agents_context(seed_agents)
        prompt = ANSWER_PROMPT.format(question=question, context=ctx)
        self.provider.begin_question()
        answers = await asyncio.gather(
            *(self.provider.complete(m, prompt) for m in models)
        )
        extra = max(0, n - len(models) - 1)
        if extra:
            extras = await asyncio.gather(
                *(self.provider.complete(models[0], prompt) for _ in range(extra))
            )
            answers = list(answers) + list(extras)
        synthesized = await self.provider.complete(
            self.chairman,
            SYNTHESIZE_PROMPT.format(
                question=question,
                answers="\n".join(f"- {a}" for a in answers),
            ),
        )
        report = self.provider.question_report()
        return EngineOutcome(
            decision=synthesized,
            rounds_taken=1,
            requests=report.requests,
            transcript=list(answers),
        )
