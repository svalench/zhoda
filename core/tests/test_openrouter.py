"""Provider tests — mocked transport, no live API calls (Cursor rule).
Round-3: 429 handling, budget cap, retry policy.
Round-4: per-question budget delta, pre-call estimate.
"""

import asyncio

import httpx
import pytest

from zhoda_core.providers.openrouter import (
    BudgetExceededError,
    OpenRouterProvider,
    QuotaExceededError,
    ZhodaProviderError,
)


def make_provider(handler: httpx.MockTransport, **kwargs) -> OpenRouterProvider:
    provider = OpenRouterProvider(api_key="test-key", **kwargs)
    provider._client = httpx.AsyncClient(transport=handler)
    return provider


OK_RESPONSE = {
    "choices": [{"message": {"content": "ok"}}],
    "usage": {"prompt_tokens": 3, "completion_tokens": 5, "cost": 0.0},
}

PAID_RESPONSE = {
    "choices": [{"message": {"content": "ok"}}],
    "usage": {"prompt_tokens": 3, "completion_tokens": 5, "cost": 0.01},
}


@pytest.mark.asyncio
async def test_429_transient_then_success() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json=OK_RESPONSE)

    provider = make_provider(httpx.MockTransport(handler))
    assert await provider.complete("m:free", "hi") == "ok"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_429_persistent_raises_quota() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "0"})

    provider = make_provider(httpx.MockTransport(handler), max_retries=2)
    with pytest.raises(QuotaExceededError):
        await provider.complete("m:free", "hi")


@pytest.mark.asyncio
async def test_4xx_fails_immediately_without_retry() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, json={"error": "invalid key"})

    provider = make_provider(httpx.MockTransport(handler))
    with pytest.raises(ZhodaProviderError):
        await provider.complete("m:free", "hi")
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_budget_zero_blocks_paid_models() -> None:
    provider = make_provider(httpx.MockTransport(lambda r: httpx.Response(200, json=OK_RESPONSE)))
    with pytest.raises(BudgetExceededError):
        await provider.complete("openai/gpt-5", "hi")


@pytest.mark.asyncio
async def test_usage_is_accumulated() -> None:
    provider = make_provider(httpx.MockTransport(lambda r: httpx.Response(200, json=OK_RESPONSE)))
    await provider.complete("m:free", "one")
    await provider.complete("m:free", "two")
    assert provider.cost.requests == 2
    assert provider.cost.tokens_in == 6
    assert provider.cost.tokens_out == 10


@pytest.mark.asyncio
async def test_budget_is_per_question_not_per_process() -> None:
    """Round-4 §1: begin_question() snapshots spend; the cap checks the delta."""
    provider = make_provider(
        httpx.MockTransport(lambda r: httpx.Response(200, json=PAID_RESPONSE)),
        budget_usd=0.02,
        prices={"paid/model": 0.0},  # zero estimate -> only accumulated delta matters
    )
    provider.begin_question()
    await provider.complete("paid/model", "one")   # +$0.01
    await provider.complete("paid/model", "two")   # +$0.01 -> at cap, allowed
    with pytest.raises(BudgetExceededError):
        await provider.complete("paid/model", "three")  # delta $0.02 >= cap
    provider.begin_question()  # new question -> fresh delta
    await provider.complete("paid/model", "four")  # works again


@pytest.mark.asyncio
async def test_precall_estimate_blocks_before_call() -> None:
    """Round-4 §2: estimate (max_tokens x price) is checked BEFORE the call."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=PAID_RESPONSE)

    provider = make_provider(
        httpx.MockTransport(handler), budget_usd=0.005, prices={"paid/model": 0.01},
    )
    provider.begin_question()
    with pytest.raises(BudgetExceededError):  # estimate 0.01 * 2 = $0.02 > cap
        await provider.complete("paid/model", "hi")
    assert calls["n"] == 0  # no request was made


@pytest.mark.asyncio
async def test_concurrent_completes_cannot_exceed_budget() -> None:
    """Параллельные complete резервируют estimate до HTTP: два вызова уже сверх капа."""
    provider = make_provider(
        httpx.MockTransport(lambda r: httpx.Response(200, json=PAID_RESPONSE)),
        budget_usd=0.015,
        prices={"paid/model": 0.005},  # estimate = 0.005 * 2 = $0.01
        max_concurrency=4,
    )
    provider.begin_question()
    results = await asyncio.gather(
        provider.complete("paid/model", "a"),
        provider.complete("paid/model", "b"),
        provider.complete("paid/model", "c"),
        return_exceptions=True,
    )
    successes = [r for r in results if r == "ok"]
    exceeded = [r for r in results if isinstance(r, BudgetExceededError)]
    assert len(successes) == 1
    assert len(exceeded) == 2
    assert provider.question_usd <= 0.015


@pytest.mark.asyncio
async def test_latency_s_is_measured() -> None:
    provider = make_provider(httpx.MockTransport(lambda r: httpx.Response(200, json=OK_RESPONSE)))
    provider.begin_question()
    await provider.complete("m:free", "hi")
    report = provider.question_report()
    assert report.latency_s >= 0.0
    assert report.requests == 1


@pytest.mark.asyncio
async def test_same_cache_key_skips_http() -> None:
    """Повтор complete с тем же cache_key не идёт в сеть."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        calls["n"] += 1
        return httpx.Response(200, json=OK_RESPONSE)

    provider = make_provider(httpx.MockTransport(handler))
    provider.begin_question()
    first = await provider.complete("m:free", "hi", cache_key="k1")
    second = await provider.complete("m:free", "hi", cache_key="k1")
    assert first == second == "ok"
    assert calls["n"] == 1
    assert provider.question_report().cache_hits == 1
    assert provider.question_report().requests == 1
