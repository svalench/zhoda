"""Stage 2: faction formation — with internal synthesis and a cheap prefilter.

A cluster of 2+ models SYNTHESIZES its platform from all member positions
(one call by the faction speaker). Pairwise structural comparison by a
conflict-checked judge is the source of truth — but only for CANDIDATE pairs
(round-7 §11): a near-identical token overlap (Jaccard >= 0.9, TODO-calibrate)
merges without spending a judge call.
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

SYNTHESIS_PROMPT = """Several council members independently took the same position.
Their individual answers:
{answers}

Synthesize the shared platform position. ONLY valid JSON:
{{"thesis": "...", "answer": "...", "arguments": ["..."],
  "falsifiability": "...", "confidence": 0.0}}"""


def near_identical(a: str, b: str, threshold: float = 0.9) -> bool:
    """Cheap prefilter (round-7 §11): token-overlap Jaccard. TODO(calibrate)."""
    ta, tb = set(a.lower().split()), set(b.lower().split())
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= threshold


class Faction(BaseModel):
    name: str = ""                                  # alias until the chairman names it
    members: list[str] = Field(default_factory=list)  # anonymized aliases
    platform: Position | None = None                # synthesized for 2+ members


class FactionClusterer:
    def __init__(self, provider: OpenRouterProvider) -> None:
        self.provider = provider
        self.divergences: list[Disagreement] = []

    async def cluster(
        self,
        positions: list[Position],
        *,
        judges: Judges,
        speakers: dict[str, str],
    ) -> list[Faction]:
        n = len(positions)
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
        candidates = [
            (i, j) for i, j in pairs
            if not near_identical(positions[i].thesis, positions[j].thesis)
        ]
        for i, j in pairs:
            if (i, j) not in candidates:
                parent[find(j)] = find(i)  # prefilter: near-identical, no judge call

        results = await asyncio.gather(
            *(self._compare(positions[i], positions[j], judges) for i, j in candidates),
            return_exceptions=True,
        )
        for (i, j), res in zip(candidates, results, strict=True):
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

        factions = []
        for members in groups.values():
            if len(members) >= 2:
                platform = await self._synthesize(members, speakers)
            else:
                platform = members[0]
            factions.append(Faction(
                name=members[0].model,
                members=[p.model for p in members],
                platform=platform,
            ))
        return factions

    async def _compare(self, a: Position, b: Position, judges: Judges) -> dict:
        probe = Faction(name="probe", members=[a.model, b.model])
        return await self.provider.ask_json(
            judges.for_faction(probe),
            PAIRWISE_PROMPT.format(a=a.thesis, b=b.thesis),
            cache_key=make_cache_key("pair", a.thesis, b.thesis),
        )

    async def _synthesize(self, members: list[Position], speakers: dict[str, str]) -> Position:
        """Internal round: the faction's collective platform from all members."""
        speaker = speakers.get(members[0].model)
        if speaker is None:
            return members[0]
        answers = "\n\n".join(f"- {p.thesis}: {p.answer}" for p in members)
        data = await self.provider.ask_json(
            speaker,
            SYNTHESIS_PROMPT.format(answers=answers),
            cache_key=make_cache_key("synth", answers),
        )
        return Position(model=members[0].model, **data)
