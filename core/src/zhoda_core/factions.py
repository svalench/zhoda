"""Stage 2: faction formation.

Source of truth is a pairwise STRUCTURAL comparison by a judge ("same
position? if not — what is the divergence?"); divergences seed dissent_map.
Factions form bottom-up (union-find) — never assigned roles. Position
comparison is one of the three auditable trust points (round-3 §6); the
judge is conflict-checked per faction (round-5 §2).
"""

import asyncio

from pydantic import BaseModel, Field

from .judges import Judges
from .models import Disagreement, Position
from .providers.openrouter import OpenRouterProvider, make_cache_key

PAIRWISE_PROMPT = """Position A thesis: {a}
Position B thesis: {b}

For practical purposes, are these the same position? Respond with ONLY valid JSON:
{{"same": true, "divergence": ""}} or {{"same": false, "divergence": "one sentence"}}"""


class Faction(BaseModel):
    name: str = ""                                  # alias of first member until named
    members: list[str] = Field(default_factory=list)  # anonymized aliases
    platform: Position | None = None                # shared platform answer


class FactionClusterer:
    def __init__(self, provider: OpenRouterProvider) -> None:
        self.provider = provider
        self.divergences: list[Disagreement] = []

    async def cluster(self, positions: list[Position], *, judges: Judges) -> list[Faction]:
        n = len(positions)
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
        results = await asyncio.gather(
            *(self._compare(positions[i], positions[j], judges) for i, j in pairs),
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

    async def _compare(self, a: Position, b: Position, judges: Judges) -> dict:
        probe = Faction(name="probe", members=[a.model, b.model])
        return await self.provider.ask_json(
            judges.for_faction(probe),
            PAIRWISE_PROMPT.format(a=a.thesis, b=b.thesis),
            cache_key=make_cache_key("pair", a.thesis, b.thesis),
        )
