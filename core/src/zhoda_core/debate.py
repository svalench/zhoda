"""Stage 3: Oxford-style debate rounds with anti-capitulation.

Objection ledger (hardened in rounds 3-4):
- Quality gate: structural prefilter here; SEMANTIC validation and closure
  are judged by the same audited judge model (round-4 §4) — string length
  alone never blocked verbose vagueness, and we stopped claiming it does.
- Closure requires a rebuttal FROM THE TARGET FACTION (round-4 §5) — the
  objection's author can't close their own charge.
- A switch needs BOTH halves (round-4 §6): an OPEN objection by ID targeting
  the model's current faction AND a non-empty cited argument.
"""

import asyncio
from uuid import uuid4

from pydantic import BaseModel, Field

from .factions import Faction
from .models import Critique, FactionSwitch, FlawType, ObjectionStatus
from .providers.openrouter import OpenRouterProvider

MIN_CLAIM_LEN = 20

CRITIQUE_PROMPT = """You represent faction \"{name}\". Platform thesis: {platform}
Strongest opposing faction \"{opponent}\": {opponent_thesis}

Produce ONE concrete critique of the opposing position. ONLY valid JSON:
{{"target_faction": "{opponent}", "flaw_type": "factual|logical|scope|values_mismatch",
  "claim": "the specific statement you dispute",
  "specifics": "what exactly is missing (required for scope/values_mismatch)"}}"""

REBUTTAL_PROMPT = """Your faction \"{name}\" platform thesis: {platform}
An objection was raised ({flaw_type}): {claim} {specifics}

Rebut it concisely. If you genuinely cannot, answer with CONCEDE."""

CLOSURE_PROMPT = """Objection ({flaw_type}): {claim} {specifics}
Rebuttal: {rebuttal}

Did the rebuttal substantively address the objection? ONLY valid JSON:
{{"closed": true}} or {{"closed": false}}"""

SWITCH_PROMPT = """An open objection stands against your faction's position.
Objection: {claim}
Your rebuttal failed to close it.
Opposing thesis: {opponent_thesis}

Do you switch factions? ONLY valid JSON:
{{"switch": true, "convinced_by": "the exact argument that convinced you"}}
or {{"switch": false, "convinced_by": ""}}"""


class Round(BaseModel):
    number: int
    critiques: list[Critique] = Field(default_factory=list)
    switches: list[FactionSwitch] = Field(default_factory=list)


class DebateEngine:
    def __init__(
        self,
        provider: OpenRouterProvider,
        judge_model: str,
        devils_advocate: bool = True,
    ) -> None:
        self.provider = provider
        self.judge_model = judge_model
        self.devils_advocate = devils_advocate
        self.objections: list[Critique] = []

    def register_critique(self, critique: Critique) -> Critique:
        """Structural prefilter + ID assignment (semantic validation: judge)."""
        if critique.flaw_type in (FlawType.FACTUAL, FlawType.LOGICAL):
            if len(critique.claim.strip()) < MIN_CLAIM_LEN:
                raise ValueError("factual/logical critique needs a concrete claim")
        elif len(critique.specifics.strip()) < MIN_CLAIM_LEN:
            raise ValueError("scope/values critique needs specifics: what exactly is missing")
        critique.id = uuid4().hex[:8]
        self.objections.append(critique)
        return critique

    def close_objection(self, objection_id: str, rebuttal: str, *, rebuttal_by: str) -> bool:
        """Close an open objection — only by a rebuttal FROM THE TARGET FACTION
        (round-4 §5). The semantic judgment is the audited judge call in
        run_round (trust point — logged to transcript)."""
        for item in self.objections:
            if item.id == objection_id and item.status == ObjectionStatus.OPEN:
                if rebuttal_by != item.target_faction:
                    return False
                item.rebuttal = rebuttal
                item.status = ObjectionStatus.CLOSED
                return True
        return False

    def validate_switch(self, switch: FactionSwitch) -> bool:
        """BOTH halves (round-4 §6): an OPEN objection by ID targeting the
        model's current faction AND a non-empty cited argument."""
        has_objection = any(
            item.id == switch.objection_id
            and item.status == ObjectionStatus.OPEN
            and item.target_faction == switch.from_faction
            for item in self.objections
        )
        return has_objection and bool(switch.convinced_by.strip())

    async def run_round(
        self,
        number: int,
        factions: list[Faction],
        *,
        speakers: dict[str, str],
    ) -> Round:
        """One round: critiques -> rebuttals -> judged closures -> switches.
        `speakers` maps anonymized alias -> real model id (per deliberation)."""
        round_ = Round(number=number)
        if len(factions) < 2:
            return round_

        ordered = sorted(factions, key=lambda f: len(f.members), reverse=True)
        leading = ordered[0]

        # 1. every faction critiques the strongest OTHER faction; the leading
        #    one attacks the runner-up (rotating devil's advocate duty)
        pairs = [(f, leading if f is not leading else ordered[1]) for f in ordered]
        raw = await asyncio.gather(
            *(self._critique(f, target, speakers) for f, target in pairs),
            return_exceptions=True,
        )
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                critique = self.register_critique(Critique(**item))
                round_.critiques.append(critique)
            except (ValueError, TypeError):
                continue  # failed the quality gate

        # 2. rebuttals from target factions + judged closure
        open_items = [c for c in self.objections if c.status == ObjectionStatus.OPEN]
        for critique in open_items:
            target = next((f for f in factions if f.name == critique.target_faction), None)
            if target is None or target.platform is None:
                continue
            speaker = speakers.get(target.members[0])
            if speaker is None:
                continue
            rebuttal = await self.provider.complete(
                speaker,
                REBUTTAL_PROMPT.format(
                    name=target.name, platform=target.platform.thesis,
                    flaw_type=critique.flaw_type, claim=critique.claim,
                    specifics=critique.specifics,
                ),
            )
            verdict = await self.provider.ask_json(
                self.judge_model,
                CLOSURE_PROMPT.format(
                    flaw_type=critique.flaw_type, claim=critique.claim,
                    specifics=critique.specifics, rebuttal=rebuttal,
                ),
            )
            if verdict.get("closed"):
                self.close_objection(critique.id, rebuttal, rebuttal_by=target.name)

        # 3. switches: only models carrying an open objection may move
        for faction in factions:
            open_against = [
                c for c in self.objections
                if c.status == ObjectionStatus.OPEN and c.target_faction == faction.name
            ]
            if not open_against or faction.platform is None:
                continue
            opponent = next((f for f in factions if f is not faction), None)
            for member in list(faction.members):
                speaker = speakers.get(member)
                if speaker is None or opponent is None or opponent.platform is None:
                    continue
                data = await self.provider.ask_json(
                    speaker,
                    SWITCH_PROMPT.format(
                        claim=open_against[0].claim,
                        opponent_thesis=opponent.platform.thesis,
                    ),
                )
                if not data.get("switch"):
                    continue
                switch = FactionSwitch(
                    model=member, from_faction=faction.name, to_faction=opponent.name,
                    convinced_by=data.get("convinced_by", ""),
                    objection_id=open_against[0].id,
                )
                if self.validate_switch(switch):
                    faction.members.remove(member)
                    opponent.members.append(member)
                    round_.switches.append(switch)
        return round_

    async def _critique(
        self, faction: Faction, target: Faction, speakers: dict[str, str],
    ) -> dict | None:
        if faction.platform is None or target.platform is None:
            return None
        speaker = speakers.get(faction.members[0])
        if speaker is None:
            return None
        return await self.provider.ask_json(
            speaker,
            CRITIQUE_PROMPT.format(
                name=faction.name, platform=faction.platform.thesis,
                opponent=target.name, opponent_thesis=target.platform.thesis,
            ),
        )
