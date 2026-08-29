"""Stage 4: consensus (zhoda) detection.

Round-8 §5: the most important decision of the protocol is made by the judge
PAIR, like closure — `all_agree` only when every non-conflicted judge
(outside the council preferred) says so; any disagreement reads as 'not
unanimous' (safe side). No single-judge bias point.

Stability rule: UNANIMOUS (judge pair `all_agree` on theses) counts as zhoda
only if it PERSISTS for `stability_rounds` consecutive rounds. Headcount
majority does not early-stop the debate — it may become zhoda only at the
rounds cap, if the majority streak also lasted `stability_rounds`.
classify() is exposed separately for single-pass protocols (vote, red_team)
where the streak must not apply.
"""

import asyncio

from .factions import ADVOCATE_ALIAS, Faction
from .judges import Judges
from .models import ConsensusStrength, bind_user_context
from .providers.openrouter import OpenRouterProvider

AGREEMENT_PROMPT = """Here are the theses of all factions:
{theses}

Do they all share the same primary recommendation (same stack/system as the main choice)?
If recommended actions / architecture in the critical path are the same, they
agree even when labels differ (e.g. both put ACID state on PostgreSQL and
stream through Kafka).
Managed vs self-hosted, caveats, and optional complements are NOT different positions.
ONLY valid JSON:
{{"all_agree": true}} or {{"all_agree": false}}"""


class ConsensusChecker:
    """Per-question streak — created per deliberation (round-8 §1)."""

    def __init__(self, provider: OpenRouterProvider, stability_rounds: int = 2) -> None:
        self.provider = provider
        self.stability_rounds = stability_rounds
        self.user_context: str = ""
        self._unanimous_streak = 0
        self._majority_streak = 0

    @property
    def majority_is_stable(self) -> bool:
        """Headcount majority держалась `stability_rounds` подряд."""
        return self._majority_streak >= self.stability_rounds

    async def classify(self, factions: list[Faction], *, judges: Judges) -> ConsensusStrength:
        """Strength of the current agreement — no streak side effects."""
        total = sum(_voting_heads(f) for f in factions)
        top = max((_voting_heads(f) for f in factions), default=0)

        if len(factions) <= 1:
            return ConsensusStrength.UNANIMOUS
        theses = "\n".join(
            f"- {f.name}: {f.platform.thesis}\n  answer: {f.platform.answer}"
            for f in factions
            if f.platform
        )
        probe = Faction(name="probe", members=[m for f in factions for m in f.members])
        pair = judges.outside() or judges.pair_for(probe)
        votes = await asyncio.gather(
            *(
                self.provider.ask_json(
                    judge,
                    bind_user_context(
                        AGREEMENT_PROMPT.format(theses=theses),
                        self.user_context,
                    ),
                )
                for judge in pair
            ),
            return_exceptions=True,
        )
        unanimous = bool(votes) and all(isinstance(v, dict) and v.get("all_agree") for v in votes)
        if unanimous:
            return ConsensusStrength.UNANIMOUS
        if total and top / total >= 2 / 3:
            return ConsensusStrength.MAJORITY
        return ConsensusStrength.SPLIT

    async def check(
        self, factions: list[Faction], *, judges: Judges
    ) -> tuple[bool, ConsensusStrength]:
        strength = await self.classify(factions, judges=judges)
        if strength is ConsensusStrength.UNANIMOUS:
            self._unanimous_streak += 1
            self._majority_streak += 1
        elif strength is ConsensusStrength.MAJORITY:
            self._unanimous_streak = 0
            self._majority_streak += 1
        else:
            self._unanimous_streak = 0
            self._majority_streak = 0
        return self._unanimous_streak >= self.stability_rounds, strength


def _voting_heads(faction: Faction) -> int:
    """Голоса совета; зарезервированный адвокат в majority не считается."""
    return sum(1 for m in faction.members if m != ADVOCATE_ALIAS)
