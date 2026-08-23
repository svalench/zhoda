"""Stage 4: consensus (zhoda) detection.

Computed on structured theses via a judge call, never prose similarity.
Stability rule (round-2 §2): consensus counts only if agreement PERSISTS for
`stability_rounds` consecutive rounds — protection against a random flip.
Thesis comparison is one of the three auditable trust points (round-3 §6).
"""

from .factions import Faction
from .models import ConsensusStrength
from .providers.openrouter import OpenRouterProvider

AGREEMENT_PROMPT = """Here are the theses of all factions:
{theses}

Do they all state the same position for practical purposes? ONLY valid JSON:
{{"all_agree": true}} or {{"all_agree": false}}"""


class ConsensusChecker:
    def __init__(
        self,
        provider: OpenRouterProvider,
        judge_model: str,
        stability_rounds: int = 2,
    ) -> None:
        self.provider = provider
        self.judge_model = judge_model
        self.stability_rounds = stability_rounds
        self._stable_streak = 0

    async def check(self, factions: list[Faction]) -> tuple[bool, ConsensusStrength]:
        total = sum(len(f.members) for f in factions)
        top = max((len(f.members) for f in factions), default=0)

        if len(factions) <= 1:
            strength = ConsensusStrength.UNANIMOUS
        else:
            theses = "\n".join(
                f"- {f.name}: {f.platform.thesis}" for f in factions if f.platform
            )
            verdict = await self.provider.ask_json(
                self.judge_model, AGREEMENT_PROMPT.format(theses=theses),
            )
            if verdict.get("all_agree"):
                strength = ConsensusStrength.UNANIMOUS
            elif total and top / total >= 2 / 3:
                strength = ConsensusStrength.MAJORITY
            else:
                strength = ConsensusStrength.SPLIT

        agreed = strength in (ConsensusStrength.UNANIMOUS, ConsensusStrength.MAJORITY)
        self._stable_streak = self._stable_streak + 1 if agreed else 0
        return self._stable_streak >= self.stability_rounds, strength
