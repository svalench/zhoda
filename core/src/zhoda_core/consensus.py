"""Stage 4: consensus (zhoda) detection.

Round-8 §5: the most important decision of the protocol is made by the judge
PAIR, like closure — `all_agree` only when every non-conflicted judge
(outside the council preferred) says so; any disagreement reads as 'not
unanimous' (safe side). No single-judge bias point.

Stability rule: consensus counts only if agreement PERSISTS for
`stability_rounds` consecutive rounds. classify() is exposed separately for
single-pass protocols (vote, red_team) where the streak must not apply.
"""

import asyncio

from .factions import Faction
from .judges import Judges
from .models import ConsensusStrength
from .providers.openrouter import OpenRouterProvider

AGREEMENT_PROMPT = """Here are the theses of all factions:
{theses}

Do they all state the same position for practical purposes? ONLY valid JSON:
{{"all_agree": true}} or {{"all_agree": false}}"""


class ConsensusChecker:
    """Per-question streak — created per deliberation (round-8 §1)."""

    def __init__(self, provider: OpenRouterProvider, stability_rounds: int = 2) -> None:
        self.provider = provider
        self.stability_rounds = stability_rounds
        self._stable_streak = 0

    async def classify(self, factions: list[Faction], *, judges: Judges) -> ConsensusStrength:
        """Strength of the current agreement — no streak side effects."""
        total = sum(len(f.members) for f in factions)
        top = max((len(f.members) for f in factions), default=0)

        if len(factions) <= 1:
            return ConsensusStrength.UNANIMOUS
        theses = "\n".join(f"- {f.name}: {f.platform.thesis}" for f in factions if f.platform)
        probe = Faction(name="probe", members=[m for f in factions for m in f.members])
        pair = judges.outside() or judges.pair_for(probe)
        votes = await asyncio.gather(
            *(self.provider.ask_json(judge, AGREEMENT_PROMPT.format(theses=theses))
              for judge in pair),
            return_exceptions=True,
        )
        unanimous = bool(votes) and all(
            isinstance(v, dict) and v.get("all_agree") for v in votes
        )
        if unanimous:
            return ConsensusStrength.UNANIMOUS
        if total and top / total >= 2 / 3:
            return ConsensusStrength.MAJORITY
        return ConsensusStrength.SPLIT

    async def check(self, factions: list[Faction], *, judges: Judges) -> tuple[bool, ConsensusStrength]:
        strength = await self.classify(factions, judges=judges)
        agreed = strength in (ConsensusStrength.UNANIMOUS, ConsensusStrength.MAJORITY)
        self._stable_streak = self._stable_streak + 1 if agreed else 0
        return self._stable_streak >= self.stability_rounds, strength
