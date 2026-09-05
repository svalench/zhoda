"""OpenRouter provider: BYOK, concurrency-limited, honest about money and quotas.

Round-3: 429 transient vs quota, real usage accounting, 4xx fail-fast.
Round-4: per-question budget (begin_question + delta), pre-call estimate.
Round-5: question_report() delta; sha256 cache keys.
Round-7: optional persistent sqlite cache (cache_path) — in-memory dies with
the process; ask_json requires a top-level JSON OBJECT (all our prompts ask
for objects; an array was silently accepted before).
"""

import asyncio
import hashlib
import json
import os
import sqlite3
import time
import uuid

import httpx

from ..env import load_zhoda_env
from ..models import AccountingStatus, CostReport, RunContext

DEFAULT_MAX_TOKENS = 2000


def make_cache_key(*parts: object) -> str:
    """Process-stable cache key."""
    return hashlib.sha256("::".join(str(p) for p in parts).encode()).hexdigest()[:16]


class ZhodaProviderError(Exception):
    """Non-retriable provider error (4xx other than 429)."""


class RateLimitError(ZhodaProviderError):
    """Transient rate limit. Retried internally with backoff."""

    def __init__(self, message: str, retry_after: float = 0.0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ServerError(ZhodaProviderError):
    """5xx from upstream. Retried internally."""


class QuotaExceededError(ZhodaProviderError):
    """Rate limit persists after all retries — likely the daily quota."""


class BudgetExceededError(ZhodaProviderError):
    """Per-question budget cap hit (pre-call estimate or accumulated delta)."""


class UnknownPriceError(BudgetExceededError):
    """Paid model without a price: a $0 reservation is not an estimate."""


class OverlappingRunError(ZhodaProviderError):
    """Interim guard: one in-flight run per provider. Concurrent begin is refused."""


class _Reservation:
    """Резерв одной попытки. release() идемпотентен — двойной cleanup не уходит в минус."""

    def __init__(self, ctx: RunContext, estimate: float) -> None:
        self.ctx = ctx
        self.estimate = estimate
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self.ctx.reserved_usd -= self.estimate
        self.ctx.in_flight -= 1


class OpenRouterProvider:
    BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(
        self,
        api_key: str | None = None,
        budget_usd: float = 0.0,
        max_retries: int = 3,
        max_concurrency: int = 4,
        prices: dict[str, float | dict[str, float]] | None = None,
        cache_path: str | None = None,
    ) -> None:
        if not api_key:
            load_zhoda_env()
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY is required (BYOK)")
        self.budget_usd = budget_usd
        self.max_retries = max_retries
        self.prices: dict[str, float | dict[str, float]] = prices or {}
        self.cost = CostReport()
        self._question_start = CostReport()
        self._question_t0: float | None = None
        self._cache: dict[str, str] = {}
        self._db: sqlite3.Connection | None = None
        if cache_path:
            self._db = sqlite3.connect(cache_path)
            self._db.execute("CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, v TEXT)")
        self._active_run: RunContext | None = None
        self._closed_run: RunContext | None = None
        self._budget_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._client = httpx.AsyncClient(timeout=120.0)

    def begin_question(self) -> RunContext:
        """Открыть run. In-flight чужой run не обнуляется — overlapping error.

        Полная изоляция по run_id в каждом complete() ломает сигнатуры; D5
        допускает явный отказ второго begin на том же provider.
        """
        if self._active_run is not None and self._active_run.in_flight > 0:
            raise OverlappingRunError(
                f"run {self._active_run.run_id} still has {self._active_run.in_flight} "
                "in-flight reservations; overlapping begin_question is refused"
            )
        self._question_start = self.cost.model_copy()
        self._question_t0 = time.monotonic()
        ctx = RunContext(run_id=uuid.uuid4().hex[:12])
        self._active_run = ctx
        return ctx

    def end_question(self) -> None:
        """Закрыть run. Резерв in-flight не трогаем здесь — его режет complete.finally."""
        if self._active_run is not None:
            self._active_run.closed = True
            self._closed_run = self._active_run
            self._active_run = None

    def _ensure_run(self) -> RunContext:
        if self._active_run is None or self._active_run.closed:
            return self.begin_question()
        return self._active_run

    def question_report(self) -> CostReport:
        """Per-question delta: what THIS deliberation spent. usd не клампится к cap."""
        started = self._question_t0
        latency = (time.monotonic() - started) if started is not None else 0.0
        ctx = self._active_run or self._closed_run
        return CostReport(
            requests=self.cost.requests - self._question_start.requests,
            tokens_in=self.cost.tokens_in - self._question_start.tokens_in,
            tokens_out=self.cost.tokens_out - self._question_start.tokens_out,
            cache_hits=self.cost.cache_hits - self._question_start.cache_hits,
            usd=self.cost.usd - self._question_start.usd,
            latency_s=latency,
            usd_status=ctx.usd_status if ctx is not None else self.cost.usd_status,
            overrun_usd=ctx.overrun_usd if ctx is not None else 0.0,
            attempts=ctx.attempts if ctx is not None else 0,
        )

    @property
    def question_usd(self) -> float:
        return self.cost.usd - self._question_start.usd

    def _prices_for(self, model: str) -> tuple[float | None, float | None]:
        """(input, output) USD / 1K tokens. Missing ≠ 0."""
        if model not in self.prices:
            return None, None
        spec = self.prices[model]
        if isinstance(spec, dict):
            raw_in = spec.get("input")
            raw_out = spec.get("output")
            in_price = float(raw_in) if raw_in is not None else None
            out_price = float(raw_out) if raw_out is not None else None
            if in_price is None and out_price is None:
                return None, None
            return in_price, out_price
        rate = float(spec)
        return rate, rate

    def _estimate(self, model: str, prompt: str, max_tokens: int) -> float:
        in_price, out_price = self._prices_for(model)
        if in_price is None and out_price is None:
            if model.endswith(":free"):
                return 0.0
            raise UnknownPriceError(
                f"no price for paid model {model!r} — zero reservation is not an estimate; "
                "set prices.input/output (or a combined rate) before a capped run"
            )
        in_tok = max(1, (len(prompt) + 3) // 4)
        in_usd = (in_price or 0.0) * in_tok / 1000.0
        out_usd = (out_price or 0.0) * max_tokens / 1000.0
        return in_usd + out_usd

    def _check_budget(self, model: str, prompt: str, max_tokens: int, ctx: RunContext) -> float:
        """Preflight admission. Возвращает estimate к резерву до HTTP."""
        if ctx.admissions_frozen:
            raise BudgetExceededError(
                f"admissions frozen after overrun ${ctx.overrun_usd:.4f} on run {ctx.run_id}"
            )
        if self.budget_usd == 0 and not model.endswith(":free"):
            raise BudgetExceededError(f"budget is $0 — only :free models allowed, got {model!r}")
        estimate = self._estimate(model, prompt, max_tokens)
        if self.budget_usd > 0:
            committed = self.question_usd + ctx.reserved_usd
            if committed + estimate >= self.budget_usd:
                raise BudgetExceededError(
                    f"estimate ${estimate:.4f} for {model} would exceed cap "
                    f"${self.budget_usd} (question already at ${self.question_usd:.4f}, "
                    f"reserved ${ctx.reserved_usd:.4f})"
                )
        return estimate

    def _cache_get(self, key: str) -> str | None:
        if key in self._cache:
            return self._cache[key]
        if self._db is not None:
            row = self._db.execute("SELECT v FROM cache WHERE k = ?", (key,)).fetchone()
            if row:
                value = row[0]
                return value if isinstance(value, str) else None
        return None

    def _cache_put(self, key: str, value: str) -> None:
        self._cache[key] = value
        if self._db is not None:
            self._db.execute("INSERT OR REPLACE INTO cache (k, v) VALUES (?, ?)", (key, value))
            self._db.commit()

    async def complete(
        self,
        model: str,
        prompt: str,
        *,
        cache_key: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> str:
        reservation: _Reservation | None = None
        async with self._budget_lock:
            ctx = self._ensure_run()
            if cache_key:
                cached = self._cache_get(cache_key)
                if cached is not None:
                    self.cost.cache_hits += 1
                    return cached
            estimate = self._check_budget(model, prompt, max_tokens, ctx)
            ctx.reserved_usd += estimate
            ctx.in_flight += 1
            reservation = _Reservation(ctx, estimate)
        try:
            async with self._semaphore:
                return await self._with_retries(
                    model, prompt, cache_key, max_tokens, ctx, estimate,
                )
        finally:
            if reservation is not None:
                async with self._budget_lock:
                    reservation.release()

    async def ask_json(
        self, model: str, prompt: str, *, cache_key: str | None = None,
    ) -> dict[str, object]:
        """Strict JSON OBJECT with one syntax-only repair retry.

        Repair asks the model to return valid JSON; it does not invent
        committed/closed/verified or treat ambiguous output as success.
        Stage DTOs decide whether the object is a valid transition.
        """
        text = await self.complete(
            model, prompt + "\n\nRespond with ONLY valid JSON. No markdown, no commentary.",
            cache_key=cache_key,
        )
        try:
            return self._extract_json(text)
        except ValueError:
            repaired = await self.complete(
                model,
                "Your previous output was not valid JSON. Return ONLY valid JSON.\n\nOutput:\n" + text,
            )
            return self._extract_json(repaired)

    @staticmethod
    def _extract_json(text: str) -> dict[str, object]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned[cleaned.find("\n") + 1:].rstrip("`").strip()
        starts = [i for i in (cleaned.find("{"), cleaned.find("[")) if i != -1]
        if not starts:
            raise ValueError("no JSON in model output")
        obj, _ = json.JSONDecoder().raw_decode(cleaned[min(starts):])
        if not isinstance(obj, dict):
            raise ValueError("expected a JSON object")
        return obj

    async def _with_retries(
        self,
        model: str,
        prompt: str,
        cache_key: str | None,
        max_tokens: int,
        ctx: RunContext,
        estimate: float,
    ) -> str:
        for attempt in range(self.max_retries):
            try:
                return await self._call(model, prompt, cache_key, max_tokens, ctx, estimate)
            except RateLimitError as exc:
                if attempt == self.max_retries - 1:
                    raise QuotaExceededError(
                        "rate limit persists after retries — likely the daily quota; "
                        "add $10 credits (1000 req/day) or wait"
                    ) from exc
                await asyncio.sleep(exc.retry_after or float(2**attempt))
            except (ServerError, httpx.TransportError):
                if attempt == self.max_retries - 1:
                    raise
                await asyncio.sleep(float(2**attempt))
        raise RuntimeError("unreachable")

    async def _call(
        self,
        model: str,
        prompt: str,
        cache_key: str | None,
        max_tokens: int,
        ctx: RunContext,
        estimate: float,
    ) -> str:
        ctx.attempts += 1
        response = await self._client.post(
            self.BASE_URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
            },
        )
        if response.status_code == 429:
            retry_after = float(response.headers.get("Retry-After") or 0)
            raise RateLimitError("429 rate limited", retry_after=retry_after)
        if 400 <= response.status_code < 500:
            raise ZhodaProviderError(f"{response.status_code}: {response.text[:200]}")
        if response.status_code >= 500:
            raise ServerError(f"{response.status_code}: {response.text[:200]}")

        data = response.json()
        self.cost.requests += 1
        usage = data.get("usage")
        if not isinstance(usage, dict):
            ctx.usd_status = AccountingStatus.UNKNOWN
            ctx.failures.append("missing_usage")
        else:
            self.cost.tokens_in += int(usage.get("prompt_tokens") or 0)
            self.cost.tokens_out += int(usage.get("completion_tokens") or 0)
            if "cost" not in usage:
                if ctx.usd_status is AccountingStatus.EXACT:
                    ctx.usd_status = AccountingStatus.PARTIAL
                ctx.failures.append("missing_cost")
            else:
                actual = float(usage["cost"] or 0.0)
                self.cost.usd += actual
                if estimate > 0 and actual > estimate:
                    ctx.overrun_usd += actual - estimate
                    ctx.admissions_frozen = True
        self.cost.usd_status = ctx.usd_status
        self.cost.overrun_usd = ctx.overrun_usd
        self.cost.attempts = ctx.attempts

        text: str = data["choices"][0]["message"]["content"]
        if cache_key:
            self._cache_put(cache_key, text)
        return text

    async def close(self) -> None:
        await self._client.aclose()
        if self._db is not None:
            self._db.close()
