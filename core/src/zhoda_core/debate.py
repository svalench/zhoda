"""Stage 3: Oxford-style debate rounds.

Order: critiques -> devil's advocate -> rebuttals -> closures -> REVISION ->
switches. Evidence discipline (round-10 §1): a URL named from memory is
labeled UNVERIFIED — null is more honest.
"""

import asyncio
import re
from uuid import uuid4

from pydantic import BaseModel, Field

from .factions import ADVOCATE_ALIAS, Faction
from .judges import Judges
from .models import (
    Critique,
    FactionSwitch,
    FlawType,
    ObjectionStatus,
    Position,
    bind_user_context,
)
from .providers.openrouter import OpenRouterProvider

MIN_CLAIM_LEN = 20

_SOURCE_RE = re.compile(r"SOURCE:\s*(https?://\S+)", re.IGNORECASE)


def extract_source(text: str) -> tuple[str, str | None]:
    """SOURCE: url → (prose without the line, url). URL без fetch = unverified."""
    match = _SOURCE_RE.search(text)
    if not match:
        return text.strip(), None
    url = match.group(1).rstrip(").,]")
    prose = _SOURCE_RE.sub("", text).strip()
    return prose, url


CRITIQUE_PROMPT = """You represent faction \"{name}\". Platform thesis: {platform}
Strongest opposing faction \"{opponent}\": {opponent_thesis}

Produce ONE concrete critique of the opposing position. ONLY valid JSON:
{{"target_faction": "{opponent}", "flaw_type": "factual|logical|scope|values_mismatch",
  "claim": "the specific statement you dispute",
  "specifics": "what exactly is missing (required for scope/values_mismatch)",
  "evidence_url": "https://source if factual, else null"}}
A URL you name from memory will be labeled UNVERIFIED, not sourced —
null is more honest. Never invent URLs."""

DEVILS_ADVOCATE_PROMPT = """You are the rotating devil's advocate. Attack the leading
position regardless of your own stance. Position of faction \"{opponent}\": {opponent_thesis}

Produce ONE concrete critique. ONLY valid JSON:
{{"target_faction": "{opponent}", "flaw_type": "factual|logical|scope|values_mismatch",
  "claim": "the specific statement you dispute",
  "specifics": "what exactly is missing (required for scope/values_mismatch)",
  "evidence_url": null}}"""

REBUTTAL_PROMPT = """Your faction \"{name}\" platform thesis: {platform}
An objection was raised ({flaw_type}): {claim} {specifics}

Rebut it concisely. If you cite a source, end with: SOURCE: <url> — it will
be labeled UNVERIFIED unless fetched. If you genuinely cannot, answer CONCEDE."""

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
{{"thesis": "...", "answer": "...",
  "claims": [{{"claim": "...", "evidence_url": null, "confidence": 0.0}}],
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

_FLAW_PRIORITY = {
    FlawType.FACTUAL: 0,
    FlawType.LOGICAL: 1,
    FlawType.SCOPE: 2,
    FlawType.VALUES_MISMATCH: 3,
}


class Round(BaseModel):
    number: int
    critiques: list[Critique] = Field(default_factory=list)
    switches: list[FactionSwitch] = Field(default_factory=list)
    revisions: list[dict] = Field(default_factory=list)
    deferred: list[dict] = Field(default_factory=list)


class DebateEngine:
    """Per-question state (objections, switches) — created per deliberation."""

    def __init__(
        self,
        provider: OpenRouterProvider,
        devils_advocate: bool = True,
        max_new_per_round: int = 3,
        max_active: int = 6,
    ) -> None:
        self.provider = provider
        self.devils_advocate = devils_advocate
        self.max_new_per_round = max_new_per_round
        self.max_active = max_active
        self.user_context: str = ""
        self.objections: list[Critique] = []
        self.switches: list[FactionSwitch] = []

    def _bind(self, prompt: str) -> str:
        return bind_user_context(prompt, self.user_context)

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

    def admit(self, critique: Critique, round_: Round) -> bool:
        """Objection cap: at most max_new_per_round per round and max_active
        open overall; overflow is marked deferred — never dropped."""
        open_now = sum(1 for c in self.objections if c.status == ObjectionStatus.OPEN)
        if len(round_.critiques) >= self.max_new_per_round or open_now >= self.max_active:
            round_.deferred.append({"claim": critique.claim, "reason": "objection cap"})
            return False
        self.register_critique(critique)
        round_.critiques.append(critique)
        return True

    def close_objection(self, objection_id: str, rebuttal: str, *, rebuttal_by: str) -> bool:
        """Close an open objection — only by a rebuttal FROM THE TARGET FACTION."""
        for item in self.objections:
            if item.id == objection_id and item.status == ObjectionStatus.OPEN:
                if rebuttal_by != item.target_faction:
                    return False
                item.rebuttal, url = extract_source(rebuttal)
                if url:
                    item.rebuttal_evidence_url = url
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

    def active_objections(self) -> list[Critique]:
        """Top-priority open objections within the active cap."""
        open_items = [c for c in self.objections if c.status == ObjectionStatus.OPEN]
        open_items.sort(key=lambda c: _FLAW_PRIORITY[c.flaw_type])
        return open_items[: self.max_active]

    async def run_round(
        self,
        number: int,
        factions: list[Faction],
        *,
        speakers: dict[str, str],
        judges: Judges,
    ) -> Round:
        round_ = Round(number=number)
        if not factions:
            return round_

        raw: list[tuple[str, object]] = []
        if len(factions) >= 2:
            ordered = sorted(factions, key=lambda f: len(f.members), reverse=True)
            leading = ordered[0]
            pairs = [(f, leading if f is not leading else ordered[1]) for f in ordered]
            raw += list(
                zip(
                    [f.name for f, _ in pairs],
                    await asyncio.gather(
                        *(self._critique(f, t, speakers, CRITIQUE_PROMPT, number) for f, t in pairs),
                        return_exceptions=True,
                    ),
                    strict=True,
                )
            )
            synthetic_opposition = any(
                f.synthetic or f.members == [ADVOCATE_ALIAS] for f in factions
            )
            if (
                self.devils_advocate
                and leading.platform is not None
                and not synthetic_opposition
            ):
                candidates = sorted(a for a in speakers if a not in leading.members)
                candidates = candidates or sorted(speakers)
                advocate_alias = candidates[(number - 1) % len(candidates)]
                da = await self._critique(
                    Faction(name=ADVOCATE_ALIAS, members=[advocate_alias]),
                    leading, speakers, DEVILS_ADVOCATE_PROMPT, number,
                )
                raw.append((ADVOCATE_ALIAS, da))
        elif self.devils_advocate and factions[0].platform is not None:
            # red_team on unanimity: attack the only platform directly
            only = factions[0]
            candidates = sorted(a for a in speakers if a not in only.members) or sorted(speakers)
            advocate_alias = candidates[(number - 1) % len(candidates)]
            da = await self._critique(
                Faction(name="devils_advocate", members=[advocate_alias]),
                only, speakers, DEVILS_ADVOCATE_PROMPT, number,
            )
            raw.append(("devils_advocate", da))

        for author, item in raw:
            if not isinstance(item, dict):
                continue
            try:
                critique = Critique(**item)
                critique.author_faction = author
                self.admit(critique, round_)
            except (ValueError, TypeError):
                continue

        async def rebut(critique: Critique) -> tuple[Critique, str] | None:
            target = next((f for f in factions if f.name == critique.target_faction), None)
            if target is None or target.platform is None:
                return None
            speaker = speakers.get(target.members[(number - 1) % len(target.members)])
            if speaker is None:
                return None
            text = await self.provider.complete(
                speaker,
                self._bind(REBUTTAL_PROMPT.format(
                    name=target.name, platform=target.platform.thesis,
                    flaw_type=critique.flaw_type, claim=critique.claim,
                    specifics=critique.specifics,
                )),
            )
            return critique, text

        rebuttals = await asyncio.gather(
            *(rebut(c) for c in self.active_objections()), return_exceptions=True,
        )

        async def judge_closure(item: tuple[Critique, str]) -> None:
            critique, rebuttal = item
            target = next(f for f in factions if f.name == critique.target_faction)
            votes = await asyncio.gather(
                *(self.provider.ask_json(
                    judge,
                    self._bind(CLOSURE_PROMPT.format(
                        flaw_type=critique.flaw_type, claim=critique.claim,
                        specifics=critique.specifics, rebuttal=rebuttal,
                    )),
                ) for judge in judges.pair_for(target)),
                return_exceptions=True,
            )
            if votes and all(isinstance(v, dict) and v.get("closed") for v in votes):
                self.close_objection(critique.id, rebuttal, rebuttal_by=target.name)
            else:
                prose, url = extract_source(rebuttal)
                critique.rebuttal = prose
                if url:
                    critique.rebuttal_evidence_url = url

        await asyncio.gather(
            *(judge_closure(item) for item in rebuttals if isinstance(item, tuple)),
            return_exceptions=True,
        )

        alias_of = {v: k for k, v in speakers.items()}

        async def revise(faction: Faction) -> tuple[Faction, dict, str] | None:
            open_against = self._open_against(faction)
            if not open_against or not faction.members or faction.platform is None:
                return None
            speaker = speakers.get(faction.members[(number - 1) % len(faction.members)])
            if speaker is None:
                return None
            objections_text = "\n".join(
                f"- [{c.flaw_type}] {c.claim} {c.specifics}" for c in open_against
            )
            data = await self.provider.ask_json(
                speaker,
                self._bind(REVISE_PROMPT.format(
                    name=faction.name, thesis=faction.platform.thesis,
                    objections=objections_text,
                )),
            )
            return faction, data, speaker

        revised = await asyncio.gather(
            *(revise(f) for f in factions), return_exceptions=True,
        )
        for item in revised:
            if not isinstance(item, tuple):
                continue
            faction, data, speaker = item
            if not (data.get("changed") and data.get("thesis")) or faction.platform is None:
                continue
            faction.platform = Position(
                model=alias_of.get(speaker, faction.platform.model),
                thesis=data["thesis"],
                answer=data.get("answer", faction.platform.answer),
                claims=faction.platform.claims,
                falsifiability=data.get("falsifiability", faction.platform.falsifiability),
                confidence=float(data.get("confidence", faction.platform.confidence)),
            )
            round_.revisions.append({
                "faction": faction.name, "change_note": data.get("change_note", ""),
            })
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
                            self._bind(WITHDRAW_PROMPT.format(
                                flaw_type=critique.flaw_type, claim=critique.claim,
                                specifics=critique.specifics,
                                thesis=faction.platform.thesis,
                            )),
                        )
                        withdrawn = bool(answer.get("withdraw"))
                if not withdrawn:
                    votes = await asyncio.gather(
                        *(self.provider.ask_json(
                            judge,
                            self._bind(SUPERSEDE_PROMPT.format(
                                flaw_type=critique.flaw_type, claim=critique.claim,
                                specifics=critique.specifics,
                                thesis=faction.platform.thesis,
                            )),
                        ) for judge in judges.pair_for(faction)),
                        return_exceptions=True,
                    )
                    if not (
                        votes
                        and all(isinstance(v, dict) and v.get("addressed") for v in votes)
                    ):
                        continue
                self.supersede_objection(critique.id)

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
                    continue
                target = next(
                    f for f in factions if f.name == objection.author_faction
                )
                data = await self.provider.ask_json(
                    speaker,
                    self._bind(SWITCH_PROMPT.format(
                        claim=objection.claim,
                        opponent_thesis=target.platform.thesis if target.platform else "",
                    )),
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
            c for c in self.active_objections()
            if c.target_faction == faction.name
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
            self._bind(prompt.format(
                name=faction.name,
                platform=faction.platform.thesis if faction.platform else "(none)",
                opponent=target.name, opponent_thesis=target.platform.thesis,
            )),
        )
