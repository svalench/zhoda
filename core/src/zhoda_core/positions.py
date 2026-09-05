"""Stage 1: independent structured positions, anonymized from the start.

Evidence discipline (round-10 §1): a URL named from memory will be labeled
UNVERIFIED — null is more honest. We never lend hallucinated links
institutional weight.
"""

import asyncio

from .models import Position, ValueMap, bind_user_context
from .providers.openrouter import OpenRouterProvider, make_cache_key

POSITION_PROMPT = """You are one member of a council. Give your independent structured stance.

Question: {question}
Context:
{context}

Open ambiguities in the user context are UNRESOLVED — never treat them as
confirmed constraints or as facts the user affirmed.
If the question embeds an unproven assertion (always/never/since/given that/
why is), do NOT treat it as a fact. Reject a false premise; do not explain
it as if it were true.

Respond with ONLY valid JSON:
{{"thesis": "core position in 1-2 sentences",
  "answer": "full answer",
  "claims": [{{"claim": "key argument",
              "evidence_url": null,
              "confidence": 0.0}}],
  "falsifiability": "conditions under which this position is wrong",
  "confidence": 0.0}}
A URL you name from memory will be labeled UNVERIFIED, not sourced —
null is more honest. Never invent URLs."""


async def extract_positions(
    provider: OpenRouterProvider,
    council: list[str],
    question: str,
    value_map: ValueMap,
    aliases: dict[str, str],
    *,
    context: str = "",
) -> list[Position]:
    async def one(model: str) -> Position:
        prompt = bind_user_context(
            POSITION_PROMPT.format(
                question=question,
                context=context.strip() or "(none)",
            ),
            value_map.as_prompt_block(),
        )
        data = await provider.ask_json(
            model,
            prompt,
            cache_key=make_cache_key("pos", model, prompt),
        )
        return Position(model=aliases[model], **data)

    results = await asyncio.gather(*(one(m) for m in council), return_exceptions=True)
    positions = [r for r in results if isinstance(r, Position)]
    if not positions:
        raise RuntimeError("all council models failed")
    return positions
