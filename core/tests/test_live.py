"""Live smoke test against the real OpenRouter API.

Runs ONLY with `pytest -m live` and a real OPENROUTER_API_KEY — never in CI's
default path (Cursor rule: no live API calls in CI).
"""

import os

import pytest

from zhoda_core.providers.openrouter import OpenRouterProvider

pytestmark = pytest.mark.live


@pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="no key")
@pytest.mark.asyncio
async def test_free_model_answers() -> None:
    provider = OpenRouterProvider()
    try:
        text = await provider.complete(
            "deepseek/deepseek-chat-v3:free", "Answer with exactly: OK",
        )
        assert text.strip()
        assert provider.cost.requests == 1
    finally:
        await provider.close()
