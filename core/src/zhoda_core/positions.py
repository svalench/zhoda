"""Stage 1: independent structured positions, anonymized from the start.

Evidence discipline (round-10 §1): a URL named from memory will be labeled
UNVERIFIED — null is more honest. We never lend hallucinated links
institutional weight.
"""

import asyncio

from .models import Position, RunCompleteness, ValueMap, bind_user_context
from .providers.openrouter import OpenRouterProvider, make_cache_key
from .stage_dtos import position_from_model

POSITION_PROMPT = """You are one member of a council. Give your independent structured stance.

Question: {question}
Context:
{context}

Open ambiguities in the user context are UNRESOLVED — never treat them as
confirmed constraints or as facts the user affirmed.
If the question embeds an unproven assertion (always/never/since/given that/
why is), treat it as unverified: not a confirmed fact and not automatically
false. Do not adopt it as a constraint. A background "given that" does not
replace the asked action.

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
    completeness: RunCompleteness | None = None,
) -> list[Position]:
    """Requested roster фиксируется до первого вызова. Ответившие ≠ знаменатель."""
    if completeness is not None:
        for model in council:
            if completeness.get("position", model) is None:
                completeness.register("position", model)

    async def one(model: str) -> Position:
        try:
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
            parsed = position_from_model(data, alias=aliases[model], prompt=prompt)
            if parsed.value is None:
                raise ValueError(parsed.error.error if parsed.error else "invalid position")
            if completeness is not None:
                completeness.succeed("position", model)
            return parsed.value
        except Exception as exc:  # noqa: BLE001 — fail the check, keep the roster
            if completeness is not None:
                completeness.fail("position", model, f"{type(exc).__name__}: {exc}"[:200])
            raise

    results = await asyncio.gather(*(one(m) for m in council), return_exceptions=True)
    positions = [r for r in results if isinstance(r, Position)]
    if not positions:
        raise RuntimeError("all council models failed")
    return positions
