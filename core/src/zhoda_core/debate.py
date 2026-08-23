"""Stage 3: Oxford-style debate rounds.

Order (round-6 §1): critiques -> devil's advocate -> rebuttals -> closures ->
REVISION -> switches against the REVISED platforms.

Round-8:
- SUPERSEDED is symmetric to CLOSED (§2): after a revision, the objection's
  AUTHOR faction is asked to withdraw (one counter-round); if it refuses,
  the non-conflicted judge PAIR must unanimously confirm the objection is
  addressed. No revision-washing: the accused can't free itself alone.
- switches pick the first open objection with a RESOLVABLE author faction
  (§4) — a devil's advocate objection no longer blocks a valid switch.
- the revision speaker ROTATES among faction members per round (§6) — members
  who joined via a switch get the pen; the faction is not 'member[0] + crowd'
  in dynamics.
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

SWITCH_PROMPT = """An open objection stands against your faction's CURRENT platform.
Objection: {claim}
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

WITHDRAW_PROMPT = """Your faction raised this objection ({flaw_type}): {claim} {specifics}
The opposing faction revised its platform. New thesis: {thesis}

Do you withdraw your objection? ONLY valid JSON:
{{"withdraw": true}} or {{"withdraw": false}}"""

SUPERSEDE_PROMPT = """Objection ({flaw_type}): {claim} {specifics}
The faction revised its platform. New thesis: {thesis}

Does the revised platform substantively address the objection? ONLY valid JSON:
{{"addressed": true}} or {{"addressed": false}}"""


class Round(BaseModel):
    number: int
    critiques: list[Critique] = Field(default_factory=list)
    switches: list[FactionSwitch] = Field(default_factory=list)
    revisions: list[dict] = Field(default_factory=list)


class DebateEngine:
    """Per-question state (objections, switches) — created per deliberation
    by the engine (round-8 §1), never shared across questions."""

    def __init__(self, provider: OpenRouterProvider, devils_advocate: bool = True) -> None:
        self.provider = provider
        self.devils_advocate = devils_advocate
        self.objections: list[Critique] = []
        self.switches: list[FactionSwitch] = []

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

    def supersede_objection(self, objection_id: str) -> bool:
        """Mark an objection as addressed by a platform revision."""
        for item in self.objections:
            if item.id == objection_id and item.status == ObjectionStatus.OPEN:
                item.status = ObjectionStatus.SUPERSEDED
                return True
        return False

    def validate_switch(self, switch: FactionSwitch) -> bool:
        """Open objection by ID targeting the current faction + non-empty
        citation + target IS the objection's author faction."""
        objection = next(
            (c for c in self.objections if c.id == switch.objection_id), None,
        )
        if objection is None or objection.status != ObjectionStatus.OPEN:
            return False
        if objection.target_faction != switch.from_faction:
            return False
        if objection.author_faction and objection.author_faction != switch.to_faction:
            return False
        return bool(switch.convinced_by.strip())

    async def run_round(
        self,
        number: int,
        factions: list[Faction],
        *,
        speakers: dict[str, str],
        judges: Judges,
    ) -> Round:
        round_ = Round(number=number)
        if len(factions) < 2:
            return round_

        ordered = sorted(factions, key=lambda f: len(f.members), reverse=True)
        leading = ordered[0]

        # 1. critiques: every faction vs the strongest OTHER faction
        pairs = [(f, leading if f is not leading else ordered[1]) for f in ordered]
        raw: list[tuple[str, object]] = list(
            zip(
                [f.name for f, _ in pairs],
                await asyncio.gather(
                    *(self._critique(f, t, speakers, CRITIQUE_PROMPT, number) for f, t in pairs),
                    return_exceptions=True,
                ),
                strict=True,
            )
        )

        # 2. devil's advocate — deterministic rotation, excludes the leading faction
        if self.devils_advocate and leading.platform is not None:
            candidates = sorted(a for a in speakers if a not in leading.members)
            candidates = candidates or sorted(speakers)
            advocate_alias = candidates[(number - 1) % len(candidates)]
            da = await self._critique(
                Faction(name="devils_advocate", members=[advocate_alias]),
                leading, speakers, DEVILS_ADVOCATE_PROMPT, number,
            )
            raw.append(("devils_advocate", da))

        for author, item in raw:
            if not isinstance(item, dict):
                continue
            try:
                critique = Critique(**item)
                critique.author_faction = author
                round_.critiques.append(self.register_critique(critique))
            except (ValueError, TypeError):
                continue

        # 3. rebuttals (parallel) + closure votes (judge pair, parallel)
        open_items = [c for c in self.objections if c.status == ObjectionStatus.OPEN]

        async def rebut(critique: Critique) -> tuple[Critique, str] | None:
            target = next((f for f in factions if f.name == critique.target_faction), None)
            if target is None or target.platform is None:
                return None
            speaker = speakers.get(target.members[0])
            if speaker is None:
                return None
            text = await self.provider.complete(
                speaker,
                REBUTTAL_PROMPT.format(
                    name=target.name, platform=target.platform.thesis,
                    flaw_type=critique.flaw_type, claim=critique.claim,
                    specifics=critique.specifics,
                ),
            )
            return critique, text

        rebuttals = await asyncio.gather(*(rebut(c) for c in open_items), return_exceptions=True)

        async def judge_closure(item: tuple[Critique, str]) -> None:
            critique, rebuttal = item
            target = next(f for f in factions if f.name == critique.target_faction)
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
            if votes and all(isinstance(v, dict) and v.get("closed") for v in votes):
                self.close_objection(critique.id, rebuttal, rebuttal_by=target.name)

        await asyncio.gather(
            *(judge_closure(item) for item in rebuttals if isinstance(item, tuple)),
            return_exceptions=True,
        )

        # 4. PLATFORM REVISION (rotating speaker) — then symmetric supersede
        async def revise(faction: Faction) -> tuple[Faction, dict] | None:
            open_against = self._open_against(faction)
            if not open_against or not faction.members or faction.platform is None:
                return None
            # rotating speaker: members who joined via a switch get the pen (§6)
            speaker = speakers.get(faction.members[(number - 1) % len(faction.members)])
            if speaker is None:
                return None
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
            return faction, data

        revised = await asyncio.gather(
            *(revise(f) for f in factions), return_exceptions=True,
        )
        for item in revised:
            if not isinstance(item, tuple):
                continue
            faction, data = item
            if not (data.get("changed") and data.get("thesis")) or faction.platform is None:
                continue
            faction.platform = Position(
                model=faction.platform.model,
                thesis=data["thesis"],
                answer=data.get("answer", faction.platform.answer),
                arguments=data.get("arguments", faction.platform.arguments),
                falsifiability=data.get("falsifiability", faction.platform.falsifiability),
                confidence=float(data.get("confidence", faction.platform.confidence)),
            )
            round_.revisions.append({
                "faction": faction.name, "change_note": data.get("change_note", ""),
            })
            # supersede, symmetric with closure (§2): author may withdraw;
            # otherwise the judge PAIR must unanimously confirm 'addressed'
            for critique in self._open_against(faction):
                author = next(
                    (f for f in factions if f.name == critique.author_faction), None,
                )
                withdrawn = False
                if author is not None and author.members:
                    author_speaker = speakers.get(author.members[0])
                    if author_speaker is not None:
                        answer = await self.provider.ask_json(
                            author_speaker,
                            WITHDRAW_PROMPT.format(
                                flaw_type=critique.flaw_type, claim=critique.claim,
                                specifics=critique.specifics,
                                thesis=faction.platform.thesis,
                            ),
                        )
                        withdrawn = bool(answer.get("withdraw"))
                if not withdrawn:
                    votes = await asyncio.gather(
                        *(self.provider.ask_json(
                            judge,
                            SUPERSEDE_PROMPT.format(
                                flaw_type=critique.flaw_type, claim=critique.claim,
                                specifics=critique.specifics,
                                thesis=faction.platform.thesis,
                            ),
                        ) for judge in judges.pair_for(faction)),
                        return_exceptions=True,
                    )
                    if not (
                        votes
                        and all(isinstance(v, dict) and v.get("addressed") for v in votes)
                    ):
                        continue  # stays open — no revision-washing
                self.supersede_objection(critique.id)

        # 5. switches — against REVISED platforms, first RESOLVABLE objection
        for faction in factions:
            open_against = self._open_against(faction)
            if not open_against or faction.platform is None:
                continue
            for member in list(faction.members):
                speaker = speakers.get(member)
                objection = next(
                    (
                        c for c in open_against
                        if any(f.name == c.author_faction for f in factions)
                    ),
                    None,
                )
                if speaker is None or objection is None:
                    continue  # e.g. only devil's advocate objections — nowhere to switch
                target = next(
                    f for f in factions if f.name == objection.author_faction
                )
                data = await self.provider.ask_json(
                    speaker,
                    SWITCH_PROMPT.format(
                        claim=objection.claim,
                        opponent_thesis=target.platform.thesis if target.platform else "",
                    ),
                )
                if not data.get("switch"):
                    continue
                switch = FactionSwitch(
                    model=member, from_faction=faction.name, to_faction=target.name,
                    convinced_by=data.get("convinced_by", ""),
                    objection_id=objection.id,
                )
                if self.validate_switch(switch):
                    faction.members.remove(member)
                    target.members.append(member)
                    round_.switches.append(switch)
                    self.switches.append(switch)
        return round_

    def _open_against(self, faction: Faction) -> list[Critique]:
        return [
            c for c in self.objections
            if c.status == ObjectionStatus.OPEN and c.target_faction == faction.name
        ]

    async def _critique(
        self,
        faction: Faction,
        target: Faction,
        speakers: dict[str, str],
        prompt: str,
        number: int,
    ) -> dict | None:
        if target.platform is None:
            return None
        speaker = speakers.get(faction.members[(number - 1) % len(faction.members)])
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
