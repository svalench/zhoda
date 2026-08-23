"""OpenRouter provider: BYOK, concurrency-limited, honest about money and quotas.

Round-3 fixes: 429 transient vs quota, real usage accounting, 4xx fail-fast.
Round-4 fixes:
- Budget is PER QUESTION: engine calls begin_question() on entry, and the cap
  checks the delta since that snapshot — not the provider's lifetime spend.
- The cap is PRE-CALL for paid models: estimate (max_tokens x price) is checked
  before the request; a single frontier call can't jump the cap unnoticed.
"""

import asyncio
import json
import os

import httpx

from ..models import CostReport

DEFAULT_MAX_TOKENS = 2000


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


class OpenRouterProvider:
    BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(
        self,
        api_key: str | None = None,
        budget_usd: float = 0.0,
        max_retries: int = 3,
        max_concurrency: int = 4,
        prices: dict[str, float] | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY is required (BYOK)")
        self.budget_usd = budget_usd
        self.max_retries = max_retries
        self.prices = prices or {}  # model -> USD per 1K output tokens
        self.cost = CostReport()
        self._question_start_usd = 0.0
        self._cache: dict[str, str] = {}
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._client = httpx.AsyncClient(timeout=120.0)

    def begin_question(self) -> None:
        """Snapshot spend at the start of a deliberation (round-4 §1)."""
        self._question_start_usd = self.cost.usd

    @property
    def question_usd(self) -> float:
        return self.cost.usd - self._question_start_usd

    def _check_budget(self, model: str, max_tokens: int) -> None:
        if self.budget_usd == 0 and not model.endswith(":free"):
            raise BudgetExceededError(f"budget is $0 — only :free models allowed, got {model!r}")
        if self.budget_usd > 0:
            estimate = self.prices.get(model, 0.0) * (max_tokens / 1000)
            if self.question_usd + estimate > self.budget_usd:
                raise BudgetExceededError(
                    f"estimate ${estimate:.4f} for {model} would exceed cap "
                    f"${self.budget_usd} (question already at ${self.question_usd:.4f})"
                )

    async def complete(
        self,
        model: str,
        prompt: str,
        *,
        cache_key: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> str:
        if cache_key and cache_key in self._cache:
            self.cost.cache_hits += 1
            return self._cache[cache_key]
        self._check_budget(model, max_tokens)
        async with self._semaphore:
            return await self._with_retries(model, prompt, cache_key, max_tokens)

    async def ask_json(self, model: str, prompt: str, *, cache_key: str | None = None) -> dict:
        """Strict JSON output with one repair retry (Cursor rule 10-python-core)."""
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
    def _extract_json(text: str) -> dict:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned[cleaned.find("\n") + 1:].rstrip("`").strip()
        starts = [i for i in (cleaned.find("{"), cleaned.find("[")) if i != -1]
        if not starts:
            raise ValueError("no JSON in model output")
        obj, _ = json.JSONDecoder().raw_decode(cleaned[min(starts):])
        return obj

    async def _with_retries(self, model: str, prompt: str, cache_key: str | None, max_tokens: int) -> str:
        for attempt in range(self.max_retries):
            try:
                return await self._call(model, prompt, cache_key, max_tokens)
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

    async def _call(self, model: str, prompt: str, cache_key: str | None, max_tokens: int) -> str:
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
