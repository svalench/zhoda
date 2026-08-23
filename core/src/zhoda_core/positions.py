"""Stage 1: independent structured positions, anonymized from the start.

Evidence discipline (values №1): every claim carries `evidence_url` — null
means 'assumption', and renders label it accordingly.
"""

import asyncio

from .models import Position, ValueMap
from .providers.openrouter import OpenRouterProvider, make_cache_key

POSITION_PROMPT = """You are one member of a council. Give your independent structured stance.

Question: {question}
Context (value map): {value_map}

Respond with ONLY valid JSON:
{{"thesis": "core position in 1-2 sentences",
  "answer": "full answer",
  "claims": [{{"claim": "key argument",
              "evidence_url": "https://source or null",
              "confidence": 0.0}}],
  "falsifiability": "conditions under which this position is wrong",
  "confidence": 0.0}}
If you have no source for a claim, set evidence_url to null — the report
will label it an assumption. Never invent URLs."""


async def extract_positions(
    provider: OpenRouterProvider,
    council: list[str],
    question: str,
    value_map: ValueMap,
    aliases: dict[str, str],
) -> list[Position]:
    async def one(model: str) -> Position:
        data = await provider.ask_json(
            model,
            POSITION_PROMPT.format(question=question, value_map=value_map.model_dump()),
            cache_key=make_cache_key("pos", model, question),
        )
        return Position(model=aliases[model], **data)

    results = await asyncio.gather(*(one(m) for m in council), return_exceptions=True)
    positions = [r for r in results if isinstance(r, Position)]
    if not positions:
        raise RuntimeError("all council models failed")
    return positions
