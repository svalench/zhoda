"""Stage 3: Oxford-style debate rounds with anti-capitulation.

Objection ledger (critique §2): a Critique opens an objection; a rebuttal
must reference the claim to close it. A faction switch is VALID only if the
model carries an OPEN objection it failed to close within the round — the
protocol judges 'unclosed objection', never 'persuasiveness'. This is the
defense against agents capitulating to a confident majority.
"""

from pydantic import BaseModel, Field

from .models import Critique, FactionSwitch, ObjectionStatus
from .factions import Faction
from .providers.openrouter import OpenRouterProvider


class Round(BaseModel):
    number: int
    critiques: list[Critique] = Field(default_factory=list)
    switches: list[FactionSwitch] = Field(default_factory=list)


class DebateEngine:
    def __init__(self, provider: OpenRouterProvider, devils_advocate: bool = True) -> None:
        self.provider = provider
        self.devils_advocate = devils_advocate
        self.objections: list[Critique] = []  # the ledger

    async def run_round(self, number: int, factions: list[Faction]) -> Round:
        """One round: argument -> rebuttal -> cross-examination -> switches.
        Devil's advocate (rotating) must attack the leading faction.
        All critique targets are anonymized aliases."""
        raise NotImplementedError  # TODO(mvp)

    def validate_switch(self, switch: FactionSwitch) -> bool:
        """A switch is valid only with an open, unclosed objection behind it."""
        open_objections = [
            c for c in self.objections
            if c.status == ObjectionStatus.OPEN and c.target_faction == switch.from_faction
        ]
        return bool(open_objections) and bool(switch.failed_rebuttal)
