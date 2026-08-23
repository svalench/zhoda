"""Stage 3: Oxford-style debate rounds with anti-capitulation.

Objection ledger (round-2 §2, hardened in round-3 §5):
- A critique passes a quality gate: factual/logical need a concrete claim;
  scope/values_mismatch need `specifics` (what exactly is missing). Vague
  objections can't be spammed to force switches.
- A faction switch is valid only against an OPEN objection referenced by ID,
  targeting the model's current faction. Judged: 'unclosed objection',
  never 'persuasiveness'.
- Objection closure is an LLM judgment — one of the three auditable trust
  points of the protocol (router, objection closure, position comparison);
  every closure is logged to the transcript.
"""

from uuid import uuid4

from pydantic import BaseModel, Field

from .factions import Faction
from .models import Critique, FactionSwitch, FlawType, ObjectionStatus
from .providers.openrouter import OpenRouterProvider

MIN_CLAIM_LEN = 20


class Round(BaseModel):
    number: int
    critiques: list[Critique] = Field(default_factory=list)
    switches: list[FactionSwitch] = Field(default_factory=list)


class DebateEngine:
    def __init__(self, provider: OpenRouterProvider, devils_advocate: bool = True) -> None:
        self.provider = provider
        self.devils_advocate = devils_advocate
        self.objections: list[Critique] = []  # the ledger

    def register_critique(self, critique: Critique) -> Critique:
        """Quality gate + ID assignment. Raises ValueError on vague critiques."""
        if critique.flaw_type in (FlawType.FACTUAL, FlawType.LOGICAL):
            if len(critique.claim.strip()) < MIN_CLAIM_LEN:
                raise ValueError("factual/logical critique needs a concrete claim")
        elif len(critique.specifics.strip()) < MIN_CLAIM_LEN:
            raise ValueError("scope/values critique needs specifics: what exactly is missing")
        critique.id = uuid4().hex[:8]
        self.objections.append(critique)
        return critique

    def close_objection(self, objection_id: str, rebuttal: str) -> bool:
        """Close an open objection. The structural check is here; the semantic
        'did the rebuttal answer it' judgment is an audited LLM call inside
        run_round (trust point — logged to transcript)."""
        for item in self.objections:
            if item.id == objection_id and item.status == ObjectionStatus.OPEN:
                item.rebuttal = rebuttal
                item.status = ObjectionStatus.CLOSED
                return True
        return False

    def validate_switch(self, switch: FactionSwitch) -> bool:
        """Valid only against an OPEN objection, by ID, targeting the model's
        current faction."""
        return any(
            item.id == switch.objection_id
            and item.status == ObjectionStatus.OPEN
            and item.target_faction == switch.from_faction
            for item in self.objections
        )

    async def run_round(self, number: int, factions: list[Faction]) -> Round:
        """One round: argument -> rebuttal -> cross-examination -> switches.
        Devil's advocate (rotating) must attack the leading faction.
        All critique targets are anonymized aliases (see anonymize.py)."""
        raise NotImplementedError  # TODO(mvp)
