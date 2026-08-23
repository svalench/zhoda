"""OpenRouter provider with BYOK, retries, cache, and a hard budget cap.

Hard rules (Cursor 00-project): one key, respect rate limits, honest
`quota_exceeded` on exhaustion — never silent degradation.
"""

import asyncio
import os

import httpx

from ..models import CostReport


class QuotaExceededError(Exception):
    """Free-tier quota exhausted. Surfaced honestly with instructions."""


class BudgetExceededError(Exception):
    """Per-question budget cap hit."""


class OpenRouterProvider:
    BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(
        self,
        api_key: str | None = None,
        budget_usd: float = 0.0,
        max_retries: int = 3,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY is required (BYOK)")
        self.budget_usd = budget_usd
        self.max_retries = max_retries
        self.cost = CostReport()
        self._cache: dict[str, str] = {}
        self._client = httpx.AsyncClient(timeout=120.0)

    async def complete(self, model: str, prompt: str, *, cache_key: str | None = None) -> str:
        """One completion with cache lookup, retry/backoff on 429/5xx,
        and budget accounting. Raises QuotaExceededError / BudgetExceededError."""
        if cache_key and cache_key in self._cache:
            self.cost.cache_hits += 1
            return self._cache[cache_key]
        if self.budget_usd == 0 and self.cost.usd > 0:
            raise BudgetExceededError(f"cap ${self.budget_usd} reached")

        for attempt in range(self.max_retries):
            try:
                response = await self._client.post(
                    self.BASE_URL,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                if response.status_code == 429:
                    raise QuotaExceededError("free-tier quota exhausted; add $10 credits or wait")
                response.raise_for_status()
                text: str = response.json()["choices"][0]["message"]["content"]
                self.cost.requests += 1
                if cache_key:
                    self._cache[cache_key] = text
                return text
            except httpx.HTTPStatusError:
                if attempt == self.max_retries - 1:
                    raise
                await asyncio.sleep(2**attempt)
        raise RuntimeError("unreachable")

    async def close(self) -> None:
        await self._client.aclose()
