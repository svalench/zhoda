"""Provider tests — mocked transport, no live API calls (Cursor rule).
Covers the round-3 bug fixes: 429 handling, budget cap, retry policy.
"""

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
