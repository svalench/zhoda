"""OpenRouter provider: BYOK, concurrency-limited, honest about money and quotas.

Round-3 review fixes:
- 429 on the free tier is usually a TRANSIENT rate limit (20 req/min), not a
  dead daily quota: honor Retry-After, back off, give up only after max_retries.
  RateLimitError (transient) and QuotaExceededError (persistent) are distinct
  events with distinct UX.
- The budget cap is real: usage is parsed from every response and accumulated;
  budget_usd=0 allows only :free model IDs (checked BEFORE the call).
- Retries cover 429, 5xx and network errors only. Other 4xx fail immediately —
  retrying an invalid key three times with backoff is pointless.
"""

import asyncio
import os

import httpx

from ..models import CostReport


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
    """Per-question budget cap hit."""


class OpenRouterProvider:
    BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(
        self,
        api_key: str | None = None,
        budget_usd: float = 0.0,
        max_retries: int = 3,
        max_concurrency: int = 4,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY is required (BYOK)")
        self.budget_usd = budget_usd
        self.max_retries = max_retries
        self.cost = CostReport()
        self._cache: dict[str, str] = {}
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._client = httpx.AsyncClient(timeout=120.0)

    def _check_budget(self, model: str) -> None:
        if self.budget_usd == 0 and not model.endswith(":free"):
            raise BudgetExceededError(f"budget is $0 — only :free models allowed, got {model!r}")
        if self.budget_usd > 0 and self.cost.usd >= self.budget_usd:
            raise BudgetExceededError(f"cap ${self.budget_usd} reached")

    async def complete(self, model: str, prompt: str, *, cache_key: str | None = None) -> str:
        if cache_key and cache_key in self._cache:
            self.cost.cache_hits += 1
            return self._cache[cache_key]
        self._check_budget(model)
        async with self._semaphore:
            return await self._with_retries(model, prompt, cache_key)

    async def _with_retries(self, model: str, prompt: str, cache_key: str | None) -> str:
        for attempt in range(self.max_retries):
            try:
                return await self._call(model, prompt, cache_key)
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

    async def _call(self, model: str, prompt: str, cache_key: str | None) -> str:
        response = await self._client.post(
            self.BASE_URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        )
        if response.status_code == 429:
            retry_after = float(response.headers.get("Retry-After") or 0)
            raise RateLimitError("429 rate limited", retry_after=retry_after)
        if 400 <= response.status_code < 500:
            raise ZhodaProviderError(f"{response.status_code}: {response.text[:200]}")
        if response.status_code >= 500:
            raise ServerError(f"{response.status_code}: {response.text[:200]}")

        data = response.json()
        usage = data.get("usage") or {}
        self.cost.requests += 1
        self.cost.tokens_in += int(usage.get("prompt_tokens") or 0)
        self.cost.tokens_out += int(usage.get("completion_tokens") or 0)
        self.cost.usd += float(usage.get("cost") or 0.0)  # 0.0 for :free models

        text: str = data["choices"][0]["message"]["content"]
        if cache_key:
            self._cache[cache_key] = text
        return text

    async def close(self) -> None:
        await self._client.aclose()
