"""Stage 1: independent structured positions, anonymized from the start.

Parallel fan-out with graceful degradation: a failed model is excluded,
failure only if ALL models failed (Cursor rule 10-python-core).
"""

import asyncio

from .models import Position, ValueMap
from .providers.openrouter import OpenRouterProvider

POSITION_PROMPT = """You are one member of a council. Give your independent structured stance.

Question: {question}
Context (value map): {value_map}

Respond with ONLY valid JSON:
{{"thesis": "core position in 1-2 sentences",
  "answer": "full answer",
  "arguments": ["key argument 1", "..."],
  "falsifiability": "conditions under which this position is wrong",
  "confidence": 0.0}}"""


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
            cache_key=f"pos:{model}:{hash(question)}",
        )
        return Position(model=aliases[model], **data)

    results = await asyncio.gather(*(one(m) for m in council), return_exceptions=True)
    positions = [r for r in results if isinstance(r, Position)]
    if not positions:
        raise RuntimeError("all council models failed")  # graceful degradation limit
    return positions
