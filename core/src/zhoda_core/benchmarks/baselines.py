"""Compute-matched бейзлайны: self-consistency, best-of-N, single-pass council.

Не ZhodaEngine.debate: отдельные промпты через тот же OpenRouterProvider.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Sequence

from zhoda_core.models import CostReport
from zhoda_core.providers.openrouter import OpenRouterProvider, make_cache_key

from .datasets import SeedAgent, seed_agents_context
from .runner import (
    MAX_COST_CALLS,
    EngineOutcome,
    cost_kwargs,
    cost_met,
)

ANSWER_PROMPT = """Answer the question independently. Be concise.

Question: {question}
{context}

Respond with the decision first, then a short justification."""

SC_JSON_PROMPT = """Answer independently.

Question: {question}
{context}
{options}
ONLY a JSON object:
{{"answer": "<short decision or option label>", "confidence": 0.0, "reason": "<one sentence>"}}

`answer` is the vote key. Put justification only in `reason`, never in `answer`."""

CLUSTER_PROMPT = """These answers were given independently to the same question.
Group answers that mean the same decision. Ignore wording, argument order, and length.

Question: {question}

Answers:
{answers}

ONLY JSON: {{"groups": [[1, 3], [2], [4]]}}
Indices are 1-based. Each answer belongs to exactly one group."""

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


def best_of_n_candidates(budget: int) -> int:
    """Число генераций при бюджете C: max(C-1, 1) + 1 judge = C (при C≥2)."""
    return max(budget - 1, 1)


@dataclass(frozen=True)
class SampleVote:
    """Один SC-сэмпл: голос = answer, обоснование отдельно."""

    answer: str
    confidence: float | None
    reason: str
    raw: str


def _normalize_answer(text: str) -> str:
    """Канон для сравнения меток: регистр, пунктуация, пробелы."""
    folded = text.strip().casefold()
    stripped = re.sub(r"[^\w\s]+", " ", folded, flags=re.UNICODE)
    return " ".join(stripped.split())


def parse_sample_vote(text: str) -> SampleVote:
    """JSON {answer, confidence, reason}; иначе весь текст = answer."""
    try:
        obj = OpenRouterProvider._extract_json(text)
    except (ValueError, TypeError):
        body = text.strip()
        return SampleVote(answer=body, confidence=None, reason="", raw=text)
    raw_answer = obj.get("answer")
    if raw_answer is None:
        body = text.strip()
        return SampleVote(answer=body, confidence=None, reason="", raw=text)
    answer = str(raw_answer).strip()
    if not answer:
        body = text.strip()
        return SampleVote(answer=body, confidence=None, reason="", raw=text)
    reason = obj.get("reason")
    return SampleVote(
        answer=answer,
        confidence=_parse_confidence(obj.get("confidence")),
        reason="" if reason is None else str(reason).strip(),
        raw=text,
    )


def _parse_confidence(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return max(0.0, min(float(value), 1.0))
    if isinstance(value, str):
        try:
            return max(0.0, min(float(value), 1.0))
        except ValueError:
            return None
    return None


def map_answer_to_option(answer: str, options: Sequence[str]) -> str:
    """Точное / casefold / единственный substring → метка опции."""
    if not options:
        return answer
    folded = {opt.casefold(): opt for opt in options}
    key = answer.strip().casefold()
    if key in folded:
        return folded[key]
    hits = [opt for opt in options if opt.casefold() in key or key in opt.casefold()]
    if len(hits) == 1:
        return hits[0]
    return answer


def majority_vote(votes: Sequence[SampleVote]) -> SampleVote:
    """Majority по нормализованному answer; ничья → первый сэмпл победителя."""
    keyed = [( _normalize_answer(v.answer), v) for v in votes if v.answer.strip()]
    if not keyed:
        return votes[0] if votes else SampleVote("", None, "", "")
    counts = Counter(norm for norm, _ in keyed)
    winner, _ = counts.most_common(1)[0]
    for norm, vote in keyed:
        if norm == winner:
            return vote
    return keyed[0][1]


def format_vote_decision(vote: SampleVote) -> str:
    """answer + reason — HeuristicJudge читает ключевые слова в decision."""
    if vote.reason:
        return f"{vote.answer}. {vote.reason}"
    return vote.answer


def _unique_display(keys: Iterable[str]) -> list[str]:
    """Первое вхождение каждой нормализованной метки — для судьи."""
    seen: set[str] = set()
    out: list[str] = []
    for key in keys:
        stripped = key.strip()
        if not stripped:
            continue
        norm = _normalize_answer(stripped)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(stripped)
    return out


def _groups_to_ids(groups: object, n: int) -> list[int] | None:
    """groups → id кластера на каждый индекс 1..n; мусор → None."""
    if not isinstance(groups, list):
        return None
    seen: set[int] = set()
    index_to_gid: dict[int, int] = {}
    for gid, group in enumerate(groups):
        if not isinstance(group, list):
            return None
        for item in group:
            try:
                idx = int(item)
            except (TypeError, ValueError):
                return None
            if idx < 1 or idx > n or idx in seen:
                return None
            seen.add(idx)
            index_to_gid[idx] = gid
    if seen != set(range(1, n + 1)):
        return None
    return [index_to_gid[i] for i in range(1, n + 1)]


def _sc_prompt(question: str, context: str, options: Sequence[str]) -> str:
    option_block = ""
    if options:
        listed = "\n".join(f"- {o}" for o in options)
        option_block = f"\n`answer` MUST be exactly one of:\n{listed}\n"
    return SC_JSON_PROMPT.format(
        question=question, context=context, options=option_block,
    )


def _outcome(decision: str, report: CostReport, **extra: object) -> EngineOutcome:
    return EngineOutcome(decision=decision, **cost_kwargs(report), **extra)  # type: ignore[arg-type]


async def _sample_until(
    provider: OpenRouterProvider,
    model: str,
    prompt: str,
    *,
    n_samples: int | None,
    usd_budget: float | None,
    token_budget: int | None,
) -> list[str]:
    """Compute: ровно n вызовов. Cost: пока не набрали USD/токены, кап MAX_COST_CALLS."""
    cost_mode = (usd_budget is not None and usd_budget > 0) or (
        token_budget is not None and token_budget > 0
    )
    if not cost_mode:
        n = max(n_samples or 1, 1)
        return list(
            await asyncio.gather(*(provider.complete(model, prompt) for _ in range(n)))
        )
    answers: list[str] = []
    prev_tokens, prev_usd = 0, 0.0
    while len(answers) < MAX_COST_CALLS:
        if answers and cost_met(provider.question_report(), usd_budget, token_budget):
            break
        answers.append(await provider.complete(model, prompt))
        report = provider.question_report()
        total = report.tokens_in + report.tokens_out
        if total == prev_tokens and report.usd == prev_usd:
            break
        prev_tokens, prev_usd = total, report.usd
    return answers


async def _cluster_keys(
    provider: OpenRouterProvider,
    judge_model: str,
    question: str,
    unique: Sequence[str],
) -> list[int] | None:
    """Blind judge: id кластера на каждый unique; сбой → None (голос по метке)."""
    numbered = "\n".join(f"{i}. {a}" for i, a in enumerate(unique, start=1))
    data = await provider.ask_json(
        judge_model,
        CLUSTER_PROMPT.format(question=question, answers=numbered),
        cache_key=make_cache_key("sc-cluster", question, numbered),
    )
    return _groups_to_ids(data.get("groups"), len(unique))


def _winner_from_clusters(
    votes: Sequence[SampleVote],
    unique: Sequence[str],
    cluster_ids: Sequence[int],
) -> SampleVote:
    """Majority по id кластера; ничья → первый сэмпл победившего кластера."""
    norm_to_gid = {
        _normalize_answer(label): gid for label, gid in zip(unique, cluster_ids)
    }
    gids = [norm_to_gid.get(_normalize_answer(v.answer), -1) for v in votes]
    counts = Counter(gid for gid in gids if gid >= 0)
    if not counts:
        return majority_vote(votes)
    winner_gid, _ = counts.most_common(1)[0]
    for vote, gid in zip(votes, gids):
        if gid == winner_gid:
            return vote
    return votes[0]


def _winning_confidence(
    votes: Sequence[SampleVote],
    winner: SampleVote,
    unique: Sequence[str] | None = None,
    cluster_ids: Sequence[int] | None = None,
) -> float | None:
    """Средняя confidence кластера (или метки) победителя."""
    if unique is not None and cluster_ids is not None:
        norm_to_gid = {
            _normalize_answer(label): gid for label, gid in zip(unique, cluster_ids)
        }
        winner_gid = norm_to_gid.get(_normalize_answer(winner.answer))
        confs = [
            v.confidence
            for v in votes
            if v.confidence is not None
            and norm_to_gid.get(_normalize_answer(v.answer)) == winner_gid
        ]
    else:
        target = _normalize_answer(winner.answer)
        confs = [
            v.confidence
            for v in votes
            if v.confidence is not None and _normalize_answer(v.answer) == target
        ]
    if not confs:
        return winner.confidence
    return sum(confs) / len(confs)


class SelfConsistencyArm:
    """N JSON-сэмплов одной модели, majority только по `answer`.

    Дискретные задачи: `answer_options` — голос по метке опции.
    Открытые: если меток > 1 и задан judge_model — blind-кластеризация
    смысловой эквивалентности (один ask_json сверх семплов).
    """

    def __init__(
        self,
        provider: OpenRouterProvider,
        judge_model: str | None = None,
    ) -> None:
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
        usd_budget: float | None = None,
        token_budget: int | None = None,
        answer_options: Sequence[str] = (),
    ) -> EngineOutcome:
        model = models[0]
        ctx = seed_agents_context(seed_agents)
        prompt = _sc_prompt(question, ctx, answer_options)
        self.provider.begin_question()
        answers = await _sample_until(
            self.provider,
            model,
            prompt,
            n_samples=n_samples or rounds,
            usd_budget=usd_budget,
            token_budget=token_budget,
        )
        votes = [parse_sample_vote(a) for a in answers]
        mapped = [
            SampleVote(
                answer=map_answer_to_option(v.answer, answer_options),
                confidence=v.confidence,
                reason=v.reason,
                raw=v.raw,
            )
            for v in votes
        ]
        winner = majority_vote(mapped)
        display_unique = _unique_display(v.answer for v in mapped)
        cluster_ids: list[int] | None = None
        if not answer_options and self.judge_model and len(display_unique) > 1:
            cluster_ids = await _cluster_keys(
                self.provider, self.judge_model, question, display_unique,
            )
            if cluster_ids is not None:
                winner = _winner_from_clusters(mapped, display_unique, cluster_ids)
        conf = _winning_confidence(
            mapped, winner, display_unique if cluster_ids else None, cluster_ids,
        )
        report = self.provider.question_report()
        return _outcome(
            format_vote_decision(winner),
            report,
            rounds_taken=1,
            transcript=list(answers),
            confidence=conf,
        )


class BestOfNArm:
    """Бюджет C: max(C-1, 1) генераций + 1 judge. Strict same-budget с Zhoda."""

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
        usd_budget: float | None = None,
        token_budget: int | None = None,
        answer_options: Sequence[str] = (),
    ) -> EngineOutcome:
        cost_mode = (usd_budget is not None and usd_budget > 0) or (
            token_budget is not None and token_budget > 0
        )
        del answer_options
        budget = max(n_samples or rounds, 1)
        n = best_of_n_candidates(budget)
        model = models[0]
        ctx = seed_agents_context(seed_agents)
        prompt = ANSWER_PROMPT.format(question=question, context=ctx)
        self.provider.begin_question()
        if cost_mode:
            answers = await _sample_until(
                self.provider,
                model,
                prompt,
                n_samples=None,
                usd_budget=usd_budget,
                token_budget=token_budget,
            )
        else:
            answers = list(
                await asyncio.gather(*(self.provider.complete(model, prompt) for _ in range(n)))
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
        return _outcome(chosen, report, rounds_taken=1, transcript=list(answers))


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
        usd_budget: float | None = None,
        token_budget: int | None = None,
        answer_options: Sequence[str] = (),
    ) -> EngineOutcome:
        n = n_samples or (len(models) + 1)
        del rounds, answer_options
        cost_mode = (usd_budget is not None and usd_budget > 0) or (
            token_budget is not None and token_budget > 0
        )
        ctx = seed_agents_context(seed_agents)
        prompt = ANSWER_PROMPT.format(question=question, context=ctx)
        self.provider.begin_question()
        answers = await asyncio.gather(
            *(self.provider.complete(m, prompt) for m in models)
        )
        extra = 0 if cost_mode else max(0, n - len(models) - 1)
        if extra:
            extras = await asyncio.gather(
                *(self.provider.complete(models[0], prompt) for _ in range(extra))
            )
            answers = list(answers) + list(extras)
        if cost_mode:
            more: list[str] = []
            prev_tokens, prev_usd = 0, 0.0
            while len(more) < MAX_COST_CALLS:
                report = self.provider.question_report()
                if cost_met(report, usd_budget, token_budget):
                    break
                more.append(await self.provider.complete(models[0], prompt))
                report = self.provider.question_report()
                total = report.tokens_in + report.tokens_out
                if total == prev_tokens and report.usd == prev_usd:
                    break
                prev_tokens, prev_usd = total, report.usd
            answers = list(answers) + more
        synthesized = await self.provider.complete(
            self.chairman,
            SYNTHESIZE_PROMPT.format(
                question=question,
                answers="\n".join(f"- {a}" for a in answers),
            ),
        )
        report = self.provider.question_report()
        return _outcome(synthesized, report, rounds_taken=1, transcript=list(answers))
