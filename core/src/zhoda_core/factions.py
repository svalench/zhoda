"""Stage 2: faction formation.

Embeddings are skipped in the MVP slice: the source of truth is a pairwise
STRUCTURAL comparison by a judge model ("same position? if not — what is the
divergence?"). Divergences seed the dissent_map. Factions form bottom-up
(union-find over real positions) — never assigned roles — to avoid
artificial polarization.

Position comparison is one of the three auditable trust points (round-3 §6).
"""

import asyncio

from pydantic import BaseModel, Field

from .models import Disagreement, Position
from .providers.openrouter import OpenRouterProvider

PAIRWISE_PROMPT = """Position A thesis: {a}
Position B thesis: {b}

For practical purposes, are these the same position? Respond with ONLY valid JSON:
{{"same": true, "divergence": ""}} or {{"same": false, "divergence": "one sentence"}}"""


class Faction(BaseModel):
    name: str = ""                                  # alias of first member until named
    members: list[str] = Field(default_factory=list)  # anonymized aliases
    platform: Position | None = None                # shared platform answer


class FactionClusterer:
    def __init__(self, provider: OpenRouterProvider, judge_model: str) -> None:
        self.provider = provider
        self.judge_model = judge_model
        self.divergences: list[Disagreement] = []

    async def cluster(self, positions: list[Position]) -> list[Faction]:
        n = len(positions)
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
        results = await asyncio.gather(
            *(self._compare(positions[i], positions[j]) for i, j in pairs),
            return_exceptions=True,
        )
        for (i, j), res in zip(pairs, results, strict=True):
            if not isinstance(res, dict):
                continue  # failed comparison -> keep separate (safe side)
            if res.get("same"):
                parent[find(j)] = find(i)
            elif res.get("divergence"):
                self.divergences.append(Disagreement(
                    topic=res["divergence"],
                    factions=[positions[i].model, positions[j].model],
                    summary=res["divergence"],
                ))

        groups: dict[int, list[Position]] = {}
        for i, position in enumerate(positions):
            groups.setdefault(find(i), []).append(position)
        return [
            Faction(name=members[0].model, members=[p.model for p in members], platform=members[0])
            for members in groups.values()
        ]

    async def _compare(self, a: Position, b: Position) -> dict:
        return await self.provider.ask_json(
            self.judge_model,
            PAIRWISE_PROMPT.format(a=a.thesis, b=b.thesis),
            cache_key=f"pair:{hash(a.thesis)}:{hash(b.thesis)}",
        )
