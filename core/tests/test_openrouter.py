"""Provider tests — mocked transport, no live API calls (Cursor rule).
Round-3: 429 handling, budget cap, retry policy.
Round-4: per-question budget delta, pre-call estimate.
"""

import asyncio

import httpx
import pytest

from zhoda_core.models import AccountingStatus
from zhoda_core.providers.openrouter import (
    BudgetExceededError,
    OpenRouterProvider,
    OverlappingRunError,
    QuotaExceededError,
    UnknownPriceError,
    ZhodaProviderError,
    _Reservation,
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


OVER_RESPONSE = {
    "choices": [{"message": {"content": "ok"}}],
    "usage": {"prompt_tokens": 3, "completion_tokens": 5, "cost": 0.02},
}


MISSING_USAGE_RESPONSE = {
    "choices": [{"message": {"content": "ok"}}],
}


PARTIAL_USAGE_RESPONSE = {
    "choices": [{"message": {"content": "ok"}}],
    "usage": {"prompt_tokens": 3, "completion_tokens": 5},
}


class BarrierTransport(httpx.AsyncBaseTransport):
    """HTTP hang until release — для D5 barrier, не живой биллинг."""

    def __init__(self, entered: asyncio.Event, release: asyncio.Event, payload: dict) -> None:
        self.entered = entered
        self.release = release
        self.payload = payload

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        del request
        self.entered.set()
        await self.release.wait()
        return httpx.Response(200, json=self.payload)


@pytest.mark.asyncio
async def test_d3_unknown_price_blocks_underfunded_admission() -> None:
    """D3: 4 параллельных paid без цены при cap $0.01. Admission, не post-hoc $0.08."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        calls["n"] += 1
        return httpx.Response(200, json=OVER_RESPONSE)

    provider = make_provider(
        httpx.MockTransport(handler),
        budget_usd=0.01,
        max_concurrency=4,
    )
    provider.begin_question()
    results = await asyncio.gather(
        provider.complete("paid/model", "a"),
        provider.complete("paid/model", "b"),
        provider.complete("paid/model", "c"),
        provider.complete("paid/model", "d"),
        return_exceptions=True,
    )
    assert calls["n"] == 0
    assert provider.question_usd == 0.0
    assert all(isinstance(r, UnknownPriceError) for r in results)
    assert all(isinstance(r, BudgetExceededError) for r in results)


@pytest.mark.asyncio
async def test_d4_paid_unknown_price_zero_reservation_forbidden() -> None:
    """D4: неизвестная цена ≠ $0. Строгий capped run блокируется на preflight."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        calls["n"] += 1
        return httpx.Response(200, json=PAID_RESPONSE)

    provider = make_provider(httpx.MockTransport(handler), budget_usd=0.50)
    provider.begin_question()
    with pytest.raises(UnknownPriceError):
        await provider.complete("paid/model", "hi")
    assert calls["n"] == 0
    ctx = provider._active_run
    assert ctx is not None
    assert ctx.reserved_usd == 0.0
    assert ctx.in_flight == 0


@pytest.mark.asyncio
async def test_d5_overlapping_begin_question_is_refused() -> None:
    """D5: Q2 на том же provider до конца Q1 — явная ошибка, reserved не −0.006."""
    entered = asyncio.Event()
    release = asyncio.Event()
    provider = OpenRouterProvider(
        api_key="test-key",
        budget_usd=1.0,
        prices={"paid/model": 0.003},
    )
    provider._client = httpx.AsyncClient(transport=BarrierTransport(entered, release, PAID_RESPONSE))
    q1 = provider.begin_question()
    task = asyncio.create_task(provider.complete("paid/model", "q1"))
    await entered.wait()
    reserved_during = q1.reserved_usd
    assert q1.in_flight == 1
    assert reserved_during > 0
    with pytest.raises(OverlappingRunError, match="overlapping begin_question is refused"):
        provider.begin_question()
    assert q1.reserved_usd == reserved_during
    assert q1.reserved_usd >= 0
    release.set()
    assert await task == "ok"
    assert q1.in_flight == 0
    assert q1.reserved_usd == 0.0
    q2 = provider.begin_question()
    assert q2.run_id != q1.run_id
    await provider.close()


@pytest.mark.asyncio
async def test_retry_counts_each_attempt() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json=OK_RESPONSE)

    provider = make_provider(httpx.MockTransport(handler))
    ctx = provider.begin_question()
    assert await provider.complete("m:free", "hi") == "ok"
    assert calls["n"] == 2
    assert ctx.attempts == 2
    assert provider.question_report().attempts == 2
    assert provider.question_report().requests == 1


@pytest.mark.asyncio
async def test_cancellation_releases_reservation_once() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    provider = OpenRouterProvider(
        api_key="test-key",
        budget_usd=1.0,
        prices={"paid/model": 0.003},
    )
    provider._client = httpx.AsyncClient(transport=BarrierTransport(entered, release, PAID_RESPONSE))
    ctx = provider.begin_question()
    task = asyncio.create_task(provider.complete("paid/model", "hang"))
    await entered.wait()
    assert ctx.in_flight == 1
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ctx.in_flight == 0
    assert ctx.reserved_usd == 0.0
    await provider.close()


@pytest.mark.asyncio
async def test_missing_usage_is_unknown_not_exact_zero() -> None:
    provider = make_provider(
        httpx.MockTransport(lambda r: httpx.Response(200, json=MISSING_USAGE_RESPONSE)),
    )
    provider.begin_question()
    await provider.complete("m:free", "hi")
    report = provider.question_report()
    assert report.usd == 0.0
    assert report.usd_status is AccountingStatus.UNKNOWN
    assert report.usd_status is not AccountingStatus.EXACT
    assert "missing_usage" in (provider._active_run.failures if provider._active_run else [])


@pytest.mark.asyncio
async def test_missing_cost_is_partial_accounting() -> None:
    provider = make_provider(
        httpx.MockTransport(lambda r: httpx.Response(200, json=PARTIAL_USAGE_RESPONSE)),
    )
    provider.begin_question()
    await provider.complete("m:free", "hi")
    report = provider.question_report()
    assert report.usd == 0.0
    assert report.usd_status is AccountingStatus.PARTIAL
    assert report.tokens_in == 3


@pytest.mark.asyncio
async def test_cache_hit_skips_reservation_and_attempt() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        calls["n"] += 1
        return httpx.Response(200, json=OK_RESPONSE)

    provider = make_provider(httpx.MockTransport(handler), budget_usd=1.0, prices={"m:free": 0.01})
    ctx = provider.begin_question()
    await provider.complete("m:free", "hi", cache_key="k1")
    assert ctx.attempts == 1
    assert ctx.in_flight == 0
    assert ctx.reserved_usd == 0.0
    await provider.complete("m:free", "hi", cache_key="k1")
    assert calls["n"] == 1
    assert ctx.attempts == 1
    report = provider.question_report()
    assert report.cache_hits == 1
    assert report.requests == 1
    assert report.attempts == 1


@pytest.mark.asyncio
async def test_admission_uses_input_and_output_prices() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        calls["n"] += 1
        return httpx.Response(200, json=PAID_RESPONSE)

    prompt = "x" * 4000  # ~1000 input tokens
    provider = make_provider(
        httpx.MockTransport(handler),
        budget_usd=0.03,
        prices={"paid/model": {"input": 0.02, "output": 0.01}},
    )
    provider.begin_question()
    # in 0.02 + out 0.01*2 = 0.04 >= cap 0.03 — preflight, не HTTP
    with pytest.raises(BudgetExceededError):
        await provider.complete("paid/model", prompt)
    assert calls["n"] == 0
    estimate = provider._estimate("paid/model", prompt, 2000)
    assert estimate == pytest.approx(0.04)


def test_double_cleanup_does_not_go_negative() -> None:
    from zhoda_core.models import RunContext

    ctx = RunContext(run_id="t", reserved_usd=0.006, in_flight=1)
    reservation = _Reservation(ctx, 0.006)
    reservation.release()
    reservation.release()
    assert ctx.reserved_usd == 0.0
    assert ctx.in_flight == 0


@pytest.mark.asyncio
async def test_external_overrun_freezes_admissions_without_clamping() -> None:
    over = {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 5, "cost": 1.0},
    }
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        calls["n"] += 1
        return httpx.Response(200, json=over)

    provider = make_provider(
        httpx.MockTransport(handler),
        budget_usd=5.0,
        prices={"paid/model": 0.001},
    )
    ctx = provider.begin_question()
    await provider.complete("paid/model", "hi")
    report = provider.question_report()
    assert report.usd == 1.0
    assert ctx.admissions_frozen is True
    assert ctx.overrun_usd > 0
    assert report.overrun_usd == ctx.overrun_usd
    with pytest.raises(BudgetExceededError, match="admissions frozen"):
        await provider.complete("paid/model", "again")
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_explicit_zero_price_is_a_strategy_not_unknown() -> None:
    """prices[model]=0.0 — явная стратегия; unknown freeze не срабатывает."""
    provider = make_provider(
        httpx.MockTransport(lambda r: httpx.Response(200, json=PAID_RESPONSE)),
        budget_usd=0.02,
        prices={"paid/model": 0.0},
    )
    provider.begin_question()
    await provider.complete("paid/model", "one")
    assert provider._active_run is not None
    assert provider._active_run.admissions_frozen is False
    await provider.complete("paid/model", "two")
