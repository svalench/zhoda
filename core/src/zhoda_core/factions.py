"""Stage 2: faction formation — with internal synthesis and an AUDITED prefilter.

A cluster of 2+ models SYNTHESIZES its platform from all member positions.
Pairwise structural comparison by a conflict-checked judge is the source of
truth — candidate pairs only. Round-8 §3: the Jaccard prefilter gets a
NEGATION GUARD ('use X' vs 'don't use X' never auto-merges) and every
auto-merge is recorded in `prefilter_merges` and logged to the transcript —
it must not look like a judge decision.

Per-question state (divergences, prefilter_merges): created per deliberation.
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

NEGATION_TOKENS = {"no", "not", "never", "without", "don't", "dont", "avoid", "never"}


def near_identical(a: str, b: str, threshold: float = 0.9) -> bool:
    """Cheap prefilter with a negation guard (round-8 §3). TODO(calibrate)."""
    ta, tb = set(a.lower().split()), set(b.lower().split())
    if not ta or not tb:
        return False
    if bool(ta & NEGATION_TOKENS) != bool(tb & NEGATION_TOKENS):
        return False  # one negates, the other doesn't — the judge decides
    return len(ta & tb) / len(ta | tb) >= threshold


class Faction(BaseModel):
    name: str = ""                                  # alias until the chairman names it
    members: list[str] = Field(default_factory=list)  # anonymized aliases
    platform: Position | None = None                # synthesized for 2+ members


class FactionClusterer:
    def __init__(self, provider: OpenRouterProvider) -> None:
        self.provider = provider
        self.divergences: list[Disagreement] = []
        self.prefilter_merges: list[dict[str, str]] = []  # audit trail (round-8 §3)

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
        candidates = []
        for i, j in pairs:
            if near_identical(positions[i].thesis, positions[j].thesis):
                parent[find(j)] = find(i)  # auto-merge, no judge call...
                self.prefilter_merges.append({  # ...but ALWAYS audited
                    "a": positions[i].thesis, "b": positions[j].thesis, "via": "prefilter",
                })
            else:
                candidates.append((i, j))

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
