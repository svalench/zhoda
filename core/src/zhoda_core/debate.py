"""Stage 3: Oxford-style debate rounds — WITH platform revision (round-5 §1).

The flagship stage: after critiques, rebuttals and judged closures, every
faction carrying OPEN objections REVISES its platform (or explicitly refuses
with justification). Without this, debate degenerates into attrition —
consensus was only reachable by everyone migrating to one faction, and two
platforms could never converge. Now they can.

Judging (round-5 §2): closure requires the whole non-conflicted judge pair
to agree; disagreement leaves the objection OPEN (safe side). A judge never
rules on their own faction. Devil's advocate is real: a rotating model must
attack the leading faction even if it belongs to it.
"""

import asyncio
from uuid import uuid4

from pydantic import BaseModel, Field

from .factions import Faction
from .judges import Judges
from .models import Critique, FactionSwitch, FlawType, ObjectionStatus, Position
from .providers.openrouter import OpenRouterProvider

MIN_CLAIM_LEN = 20

CRITIQUE_PROMPT = """You represent faction \"{name}\". Platform thesis: {platform}
Strongest opposing faction \"{opponent}\": {opponent_thesis}

Produce ONE concrete critique of the opposing position. ONLY valid JSON:
{{"target_faction": "{opponent}", "flaw_type": "factual|logical|scope|values_mismatch",
  "claim": "the specific statement you dispute",
  "specifics": "what exactly is missing (required for scope/values_mismatch)"}}"""

DEVILS_ADVOCATE_PROMPT = """You are the rotating devil's advocate. Attack the leading
position regardless of your own stance. Leading faction \"{opponent}\": {opponent_thesis}

Produce ONE concrete critique. ONLY valid JSON:
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

REVISE_PROMPT = """Your faction \"{name}\" platform thesis: {thesis}
Open objections that survived this round:
{objections}

Revise your platform to account for valid criticism — or keep it and justify
why the objections fail. ONLY valid JSON:
{{"thesis": "...", "answer": "...", "arguments": ["..."],
  "falsifiability": "...", "confidence": 0.0,
  "changed": true, "change_note": "what changed and why (or why not)"}}"""


class Round(BaseModel):
    number: int
    critiques: list[Critique] = Field(default_factory=list)
    switches: list[FactionSwitch] = Field(default_factory=list)
    revisions: list[dict] = Field(default_factory=list)  # platform revisions


class DebateEngine:
    def __init__(self, provider: OpenRouterProvider, devils_advocate: bool = True) -> None:
        self.provider = provider
        self.devils_advocate = devils_advocate
        self.objections: list[Critique] = []
        self.switches: list[FactionSwitch] = []  # accumulated across rounds

    def register_critique(self, critique: Critique) -> Critique:
        """Structural prefilter + ID assignment (semantic validation: judges)."""
        if critique.flaw_type in (FlawType.FACTUAL, FlawType.LOGICAL):
            if len(critique.claim.strip()) < MIN_CLAIM_LEN:
                raise ValueError("factual/logical critique needs a concrete claim")
        elif len(critique.specifics.strip()) < MIN_CLAIM_LEN:
            raise ValueError("scope/values critique needs specifics: what exactly is missing")
        critique.id = uuid4().hex[:8]
        self.objections.append(critique)
        return critique

    def close_objection(self, objection_id: str, rebuttal: str, *, rebuttal_by: str) -> bool:
        """Close an open objection — only by a rebuttal FROM THE TARGET FACTION."""
        for item in self.objections:
            if item.id == objection_id and item.status == ObjectionStatus.OPEN:
                if rebuttal_by != item.target_faction:
                    return False
                item.rebuttal = rebuttal
                item.status = ObjectionStatus.CLOSED
                return True
        return False

    def validate_switch(self, switch: FactionSwitch) -> bool:
        """BOTH halves: an OPEN objection by ID targeting the model's current
        faction AND a non-empty cited argument."""
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
        judges: Judges,
    ) -> Round:
        """critiques -> devil's advocate -> rebuttals -> judged closures ->
        switches -> PLATFORM REVISION (the stage that lets positions converge)."""
        round_ = Round(number=number)
        if len(factions) < 2:
            return round_

        ordered = sorted(factions, key=lambda f: len(f.members), reverse=True)
        leading = ordered[0]

        # 1. every faction critiques the strongest OTHER faction
        pairs = [(f, leading if f is not leading else ordered[1]) for f in ordered]
        raw = await asyncio.gather(
            *(self._critique(f, target, speakers, CRITIQUE_PROMPT) for f, target in pairs),
            return_exceptions=True,
        )

        # 2. devil's advocate: rotating model attacks the leading faction
        if self.devils_advocate and leading.platform is not None:
            advocate_alias = list(speakers)[(number - 1) % len(speakers)]
            raw.append(await self._critique(
                Faction(name="devils_advocate", members=[advocate_alias]),
                leading, speakers, DEVILS_ADVOCATE_PROMPT,
            ))

        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                round_.critiques.append(self.register_critique(Critique(**item)))
            except (ValueError, TypeError):
                continue  # failed the quality gate

        # 3. rebuttals from target factions + closure by the judge PAIR
        for critique in [c for c in self.objections if c.status == ObjectionStatus.OPEN]:
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
            votes = await asyncio.gather(
                *(self.provider.ask_json(
                    judge,
                    CLOSURE_PROMPT.format(
                        flaw_type=critique.flaw_type, claim=critique.claim,
                        specifics=critique.specifics, rebuttal=rebuttal,
                    ),
                ) for judge in judges.pair_for(target)),
                return_exceptions=True,
            )
            # disagreement between judges -> stays OPEN (safe side)
            if all(isinstance(v, dict) and v.get("closed") for v in votes) and votes:
                self.close_objection(critique.id, rebuttal, rebuttal_by=target.name)

        # 4. switches: only models carrying an open objection may move
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
                    self.switches.append(switch)

        # 5. PLATFORM REVISION — the flagship stage (round-5 §1):
        #    factions with open objections revise their platform or justify refusal
        for faction in factions:
            open_against = [
                c for c in self.objections
                if c.status == ObjectionStatus.OPEN and c.target_faction == faction.name
            ]
            if not open_against or not faction.members or faction.platform is None:
                continue
            speaker = speakers.get(faction.members[0])
            if speaker is None:
                continue
            objections_text = "\n".join(
                f"- [{c.flaw_type}] {c.claim} {c.specifics}" for c in open_against
            )
            data = await self.provider.ask_json(
                speaker,
                REVISE_PROMPT.format(
                    name=faction.name, thesis=faction.platform.thesis,
                    objections=objections_text,
                ),
            )
            if data.get("changed") and data.get("thesis"):
                faction.platform = Position(
                    model=faction.platform.model,
                    thesis=data["thesis"],
                    answer=data.get("answer", faction.platform.answer),
                    arguments=data.get("arguments", faction.platform.arguments),
                    falsifiability=data.get("falsifiability", faction.platform.falsifiability),
                    confidence=float(data.get("confidence", faction.platform.confidence)),
                )
                round_.revisions.append({
                    "faction": faction.name,
                    "change_note": data.get("change_note", ""),
                })
        return round_

    async def _critique(
        self,
        faction: Faction,
        target: Faction,
        speakers: dict[str, str],
        prompt: str,
    ) -> dict | None:
        if target.platform is None:
            return None
        speaker = speakers.get(faction.members[0])
        if speaker is None:
            return None
        return await self.provider.ask_json(
            speaker,
            prompt.format(
                name=faction.name,
                platform=faction.platform.thesis if faction.platform else "(none)",
                opponent=target.name, opponent_thesis=target.platform.thesis,
            ),
        )
