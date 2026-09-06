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
from .guards import (
    blocks_loaded_premise_switch,
    should_apply_revision,
)
from .actions import OptionCatalog, attach_action
from .claims import merge_revision_claims
from .evidence import bind_evidence, quote_span_matches
from .judges import Judges
from .models import (
    Critique,
    EvidenceBundle,
    EventType,
    FactionSwitch,
    FlawType,
    ObjectionStatus,
    Position,
    bind_user_context,
)
from .providers.openrouter import OpenRouterProvider, make_cache_key
from .replay import EventLog
from .stage_dtos import (
    AddressedVote,
    ClosedVote,
    ParseFailure,
    ReviseVote,
    SwitchVote,
    WithdrawVote,
    critique_from_model,
    engine_claims,
    parse_stage,
)

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

Produce ONE concrete critique of the opposing position.
Quote the exact phrase from the opposing thesis you dispute — do not write a
parallel essay that ignores their wording.
ONLY valid JSON:
{{"target_faction": "{opponent}", "flaw_type": "factual|logical|scope|values_mismatch",
  "claim": "the specific statement you dispute",
  "specifics": "what exactly is missing (required for scope/values_mismatch)",
  "evidence_url": "https://source if factual, else null"}}
A URL you name from memory will be labeled UNVERIFIED, not sourced —
null is more honest. Never invent URLs."""

EVIDENCE_CRITIQUE_PROMPT = """You represent faction \"{name}\". Platform thesis: {platform}
Opposing faction \"{opponent}\": {opponent_thesis}

Produce ONE evidence-focused critique of the opposing position.
If an EVIDENCE block is attached, quote an exact span from it (not from memory
and not a paraphrase). Do not write a parallel essay that ignores the source.
ONLY valid JSON:
{{"target_faction": "{opponent}", "flaw_type": "factual|logical|scope|values_mismatch",
  "claim": "the specific statement you dispute",
  "specifics": "exact evidence span and what it implies",
  "evidence_url": null}}
Do not invent URLs. A critique that ignores attached evidence is discarded."""

DEVILS_ADVOCATE_PROMPT = """You are the rotating devil's advocate. Attack the leading
position regardless of your own stance. Position of faction \"{opponent}\": {opponent_thesis}

Produce ONE concrete critique. If the thesis names a specific defect in
attached code or context, you may not dismiss it as "no evidence" — quote
the defect or concede. ONLY valid JSON:
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

Did the rebuttal substantively REFUTE the specific objection claim?
closed=true only if the rebuttal contradicts that claim with a reason.
Acknowledgment, CONCEDE, "we accept", or restating the platform is closed=false.
If the evidence block is incomplete/truncated/redacted, closed=true does NOT
verify the source — CONCEDE never proves the objection claim false.
ONLY valid JSON:
{{"closed": true}} or {{"closed": false}}"""

SWITCH_PROMPT = """An open objection stands against your faction's CURRENT platform
after a rebuttal that did NOT close it (and any revision that did not fix it).
Objection: {claim}
Opposing thesis: {opponent_thesis}

Do you switch factions? ONLY valid JSON:
{{"switch": true, "convinced_by": "quote the objection claim that convinced you"}}
or {{"switch": false, "convinced_by": "quote the part of the objection that is false"}}

Rules:
- switch=true if the objection shows your primary recommendation is wrong.
  convinced_by MUST quote words from the Objection, not restate the opposing thesis.
- switch=false only if you name a concrete fact the objection gets wrong.
- An empty convinced_by is invalid. Restating the destination thesis is not a citation.
- If the question embeds a loaded premise and your thesis already challenges it,
  switch=false: do not defect toward treating that premise as true."""

REVISE_PROMPT = """Your faction \"{name}\" platform thesis: {thesis}
Open objections that survived this round:
{objections}

Revise your platform to account for valid criticism — or keep it and justify
why the objections fail. Keep the same primary recommended action unless an
objection refutes that action. Do not replace a named pick with "it depends"
or "choose based on team expertise". Caveats may be added; the main choice stays.
If the question is A or B / A vs B, do not change the named pick — a pick
change is a faction switch, not a revision. If the question embeds a loaded
premise (always/never/since/given that/why is), do not revise toward treating
that premise as true.
ONLY valid JSON:
{{"thesis": "...", "answer": "...",
  "claims": [{{"claim": "...", "evidence_url": null, "confidence": 0.0}}],
  "falsifiability": "...", "confidence": 0.0,
  "changed": true, "change_note": "what changed and why (or why not)"}}
claims is the new platform's supporting findings: keep only claims you still
assert. Do not copy a finding you no longer stand by. Refuted claims stay in
history, not in supporting findings."""

WITHDRAW_PROMPT = """Your faction raised this objection ({flaw_type}): {claim} {specifics}
The opposing faction revised its platform. New thesis: {thesis}

Do you withdraw your objection? ONLY valid JSON:
{{"withdraw": true}} or {{"withdraw": false}}"""

SUPERSEDE_PROMPT = """Objection ({flaw_type}): {claim} {specifics}
The faction revised its platform. New thesis: {thesis}

Does the revised platform substantively FIX the objection?
Acknowledging a tradeoff, adding a caveat, or saying "it depends" while
keeping the same primary recommendation is NOT addressed.
addressed=true only if (a) the specific flaw is fixed in the new thesis, or
(b) the primary recommended action changed.
ONLY valid JSON:
{{"addressed": true}} or {{"addressed": false}}"""

_FLAW_PRIORITY = {
    FlawType.FACTUAL: 0,
    FlawType.LOGICAL: 1,
    FlawType.SCOPE: 2,
    FlawType.VALUES_MISMATCH: 3,
}


def is_concede_rebuttal(text: str) -> bool:
    """CONCEDE из промпта — не опровержение; возражение остаётся OPEN."""
    return bool(re.match(r"concede\b", (text or "").strip(), re.IGNORECASE))


def citation_quotes_objection(convinced_by: str, claim: str) -> bool:
    """Точный Unicode-span claim, не token overlap ('PostgreSQL is my favorite')."""
    return quote_span_matches(convinced_by, claim)


class Round(BaseModel):
    number: int
    critiques: list[Critique] = Field(default_factory=list)
    switches: list[FactionSwitch] = Field(default_factory=list)
    revisions: list[dict[str, str]] = Field(default_factory=list)
    deferred: list[dict[str, str]] = Field(default_factory=list)
    parse_failures: list[ParseFailure] = Field(default_factory=list)


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
        self.question: str = ""
        self.catalog: OptionCatalog | None = None
        self.evidence: EvidenceBundle | None = None
        self.events: EventLog | None = None
        self.objections: list[Critique] = []
        self.switches: list[FactionSwitch] = []
        self.parse_failures: list[ParseFailure] = []

    def _bind(self, prompt: str) -> str:
        return bind_evidence(
            bind_user_context(prompt, self.user_context),
            self.evidence,
        )

    def _emit(
        self,
        event_type: EventType,
        payload: dict[str, object],
        *,
        attribution: str = "",
    ) -> None:
        if self.events is not None:
            self.events.emit(event_type, payload, attribution=attribution)

    async def _ask(self, model: str, prompt: str) -> dict[str, object]:
        return await self.provider.ask_json(
            model, prompt, cache_key=make_cache_key("deb", model, prompt),
        )

    async def _say(self, model: str, prompt: str) -> str:
        return await self.provider.complete(
            model, prompt, cache_key=make_cache_key("deb", model, prompt),
        )

    def _record_parse(self, failure: ParseFailure | None, round_: Round) -> None:
        if failure is None:
            return
        round_.parse_failures.append(failure)
        self.parse_failures.append(failure)

    def register_critique(self, critique: Critique) -> Critique:
        """Structural prefilter + ID assignment (semantic validation: judges)."""
        if critique.flaw_type in (FlawType.FACTUAL, FlawType.LOGICAL):
            if len(critique.claim.strip()) < MIN_CLAIM_LEN:
                raise ValueError("factual/logical critique needs a concrete claim")
        elif len(critique.specifics.strip()) < MIN_CLAIM_LEN:
            raise ValueError("scope/values critique needs specifics: what exactly is missing")
        critique.id = uuid4().hex[:8]
        self.objections.append(critique)
        self._emit(
            EventType.OBJECTION_REGISTERED,
            {
                "objection_id": critique.id,
                "claim": critique.claim,
                "target_faction": critique.target_faction,
                "author_faction": critique.author_faction,
            },
            attribution=critique.author_faction,
        )
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
                self._emit(
                    EventType.OBJECTION_TRANSITION,
                    {
                        "objection_id": item.id,
                        "from_status": "open",
                        "to_status": "closed",
                    },
                    attribution=rebuttal_by,
                )
                return True
        return False

    def supersede_objection(self, objection_id: str) -> bool:
        """Mark an objection as addressed by a platform revision."""
        for item in self.objections:
            if item.id == objection_id and item.status == ObjectionStatus.OPEN:
                item.status = ObjectionStatus.SUPERSEDED
                self._emit(
                    EventType.OBJECTION_TRANSITION,
                    {
                        "objection_id": item.id,
                        "from_status": "open",
                        "to_status": "superseded",
                    },
                )
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
        quote = (switch.quote_span or switch.convinced_by).strip()
        return citation_quotes_objection(quote, objection.claim)

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
        mode: str = "oxford",
    ) -> Round:
        """oxford = полный раунд; short_review = critique+revision, без DA/rebut/switch."""
        round_ = Round(number=number)
        if not factions:
            return round_

        critique_prompt = (
            EVIDENCE_CRITIQUE_PROMPT if mode == "short_review" else CRITIQUE_PROMPT
        )
        raw: list[tuple[str, object]] = []
        if len(factions) >= 2:
            ordered = sorted(factions, key=lambda f: len(f.members), reverse=True)
            leading = ordered[0]
            pairs = [(f, leading if f is not leading else ordered[1]) for f in ordered]
            raw += list(
                zip(
                    [f.name for f, _ in pairs],
                    await asyncio.gather(
                        *(
                            self._critique(f, t, speakers, critique_prompt, number)
                            for f, t in pairs
                        ),
                        return_exceptions=True,
                    ),
                    strict=True,
                )
            )
            synthetic_opposition = any(
                f.synthetic or f.members == [ADVOCATE_ALIAS] for f in factions
            )
            real_factions = [
                f for f in factions
                if not f.synthetic and f.members != [ADVOCATE_ALIAS]
            ]
            if (
                mode != "short_review"
                and self.devils_advocate
                and leading.platform is not None
                and not synthetic_opposition
                and len(real_factions) < 2
            ):
                candidates = sorted(a for a in speakers if a not in leading.members)
                candidates = candidates or sorted(speakers)
                advocate_alias = candidates[(number - 1) % len(candidates)]
                da = await self._critique(
                    Faction(name=ADVOCATE_ALIAS, members=[advocate_alias]),
                    leading, speakers, DEVILS_ADVOCATE_PROMPT, number,
                )
                raw.append((ADVOCATE_ALIAS, da))
        elif mode == "short_review" and factions[0].platform is not None:
            only = factions[0]
            candidates = sorted(a for a in speakers if a not in only.members) or sorted(speakers)
            reviewer = candidates[(number - 1) % len(candidates)]
            review = await self._critique(
                Faction(name="reviewer", members=[reviewer]),
                only, speakers, EVIDENCE_CRITIQUE_PROMPT, number,
            )
            raw.append(("reviewer", review))
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

        allowed_factions = {f.name for f in factions}
        for author, item in raw:
            if not isinstance(item, dict):
                continue
            parsed = critique_from_model(
                item,
                author=author,
                allowed_factions=allowed_factions,
            )
            if parsed.value is None:
                self._record_parse(parsed.error, round_)
                continue
            if mode == "short_review" and not self._short_critique_has_evidence(parsed.value):
                round_.deferred.append(
                    {"claim": parsed.value.claim, "reason": "missing_evidence_span"}
                )
                continue
            try:
                self.admit(parsed.value, round_)
            except (ValueError, TypeError):
                continue

        if mode != "short_review":
            async def rebut(critique: Critique) -> tuple[Critique, str] | None:
                target = next((f for f in factions if f.name == critique.target_faction), None)
                if target is None or target.platform is None:
                    return None
                speaker = speakers.get(target.members[(number - 1) % len(target.members)])
                if speaker is None:
                    return None
                text = await self._say(
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
                if is_concede_rebuttal(rebuttal):
                    prose, url = extract_source(rebuttal)
                    critique.rebuttal = prose
                    if url:
                        critique.rebuttal_evidence_url = url
                    return
                votes = await asyncio.gather(
                    *(self._ask(
                        judge,
                        self._bind(CLOSURE_PROMPT.format(
                            flaw_type=critique.flaw_type, claim=critique.claim,
                            specifics=critique.specifics, rebuttal=rebuttal,
                        )),
                    ) for judge in judges.pair_for(target)),
                    return_exceptions=True,
                )
                flags: list[bool] = []
                for vote in votes:
                    parsed = parse_stage(
                        ClosedVote,
                        vote if isinstance(vote, dict) else None,
                        stage="closure",
                    )
                    self._record_parse(parsed.error, round_)
                    flags.append(parsed.value.closed if parsed.value is not None else False)
                if flags and all(flags):
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

        async def revise(faction: Faction) -> tuple[Faction, dict[str, object], str] | None:
            open_against = self._open_against(faction)
            if not open_against or not faction.members or faction.platform is None:
                return None
            speaker = speakers.get(faction.members[(number - 1) % len(faction.members)])
            if speaker is None:
                return None
            objections_text = "\n".join(
                f"- [{c.flaw_type}] {c.claim} {c.specifics}" for c in open_against
            )
            data = await self._ask(
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
            if faction.platform is None:
                continue
            parsed = parse_stage(ReviseVote, data, stage="revise")
            self._record_parse(parsed.error, round_)
            vote = parsed.value
            if vote is None:
                continue
            new_thesis = vote.thesis
            change_note = vote.change_note
            if not should_apply_revision(
                faction.platform.thesis,
                new_thesis,
                changed=vote.changed,
                question=self.question,
                correction=bool(self._open_against(faction)),
                change_note=change_note,
            ):
                continue
            prior_action = faction.platform.action
            new_answer = vote.answer or faction.platform.answer
            prior_claims = list(faction.platform.claims)
            incoming = engine_claims(vote.claims)
            merged = merge_revision_claims(
                prior_claims,
                incoming,
                owner=alias_of.get(speaker, faction.platform.model),
                evidence_id=(
                    self.evidence.source_id if self.evidence is not None else None
                ),
            )
            prior_by_id = {c.claim_id: c for c in prior_claims if c.claim_id}
            for rec in merged:
                old = prior_by_id.get(rec.claim_id)
                if old is None:
                    self._emit(
                        EventType.CLAIM_CREATED,
                        rec.model_dump(mode="json"),
                        attribution=faction.name,
                    )
                elif rec.state is not old.state:
                    self._emit(
                        EventType.CLAIM_TRANSITION,
                        {
                            "claim_id": rec.claim_id,
                            "from_state": old.state.value,
                            "to_state": rec.state.value,
                            "version": rec.version,
                        },
                        attribution=faction.name,
                    )
            faction.platform = Position(
                model=alias_of.get(speaker, faction.platform.model),
                thesis=new_thesis,
                answer=new_answer,
                claims=merged,
                falsifiability=vote.falsifiability or faction.platform.falsifiability,
                confidence=vote.confidence,
                action=attach_action(
                    new_thesis,
                    new_answer,
                    self.catalog,
                    prior=prior_action,
                    provenance=change_note or "revision",
                ),
            )
            round_.revisions.append({
                "faction": faction.name,
                "change_note": change_note,
            })
            if mode == "short_review":
                continue
            for critique in self._open_against(faction):
                author_faction = next(
                    (f for f in factions if f.name == critique.author_faction), None,
                )
                withdrawn = False
                if author_faction is not None and author_faction.members:
                    author_speaker = speakers.get(author_faction.members[0])
                    if author_speaker is not None:
                        answer = await self._ask(
                            author_speaker,
                            self._bind(WITHDRAW_PROMPT.format(
                                flaw_type=critique.flaw_type, claim=critique.claim,
                                specifics=critique.specifics,
                                thesis=faction.platform.thesis,
                            )),
                        )
                        parsed_w = parse_stage(WithdrawVote, answer, stage="withdraw")
                        self._record_parse(parsed_w.error, round_)
                        withdrawn = (
                            parsed_w.value.withdraw if parsed_w.value is not None else False
                        )
                if not withdrawn:
                    votes = await asyncio.gather(
                        *(self._ask(
                            judge,
                            self._bind(SUPERSEDE_PROMPT.format(
                                flaw_type=critique.flaw_type, claim=critique.claim,
                                specifics=critique.specifics,
                                thesis=faction.platform.thesis,
                            )),
                        ) for judge in judges.pair_for(faction)),
                        return_exceptions=True,
                    )
                    flags = []
                    for raw_vote in votes:
                        parsed_a = parse_stage(
                            AddressedVote,
                            raw_vote if isinstance(raw_vote, dict) else None,
                            stage="supersede",
                        )
                        self._record_parse(parsed_a.error, round_)
                        flags.append(
                            parsed_a.value.addressed if parsed_a.value is not None else False
                        )
                    if not (flags and all(flags)):
                        continue
                self.supersede_objection(critique.id)

        if mode == "short_review":
            return round_

        for faction in factions:
            open_against = self._open_against(faction)
            if not open_against or faction.platform is None:
                continue
            for member in list(faction.members):
                member_model = speakers.get(member)
                objection = next(
                    (
                        c for c in open_against
                        if any(f.name == c.author_faction for f in factions)
                    ),
                    None,
                )
                if member_model is None or objection is None:
                    continue
                target = next(
                    f for f in factions if f.name == objection.author_faction
                )
                data = await self._ask(
                    member_model,
                    self._bind(SWITCH_PROMPT.format(
                        claim=objection.claim,
                        opponent_thesis=target.platform.thesis if target.platform else "",
                    )),
                )
                parsed_s = parse_stage(SwitchVote, data, stage="switch")
                self._record_parse(parsed_s.error, round_)
                if parsed_s.value is None or not parsed_s.value.switch:
                    continue
                from_thesis = faction.platform.thesis if faction.platform else ""
                to_thesis = target.platform.thesis if target.platform else ""
                if blocks_loaded_premise_switch(self.question, from_thesis, to_thesis):
                    continue
                switch = FactionSwitch(
                    model=member, from_faction=faction.name, to_faction=target.name,
                    convinced_by=parsed_s.value.convinced_by,
                    objection_id=objection.id,
                    quote_span=parsed_s.value.convinced_by,
                    reason=parsed_s.value.convinced_by,
                    action_id=(
                        target.platform.action.action_id
                        if target.platform is not None and target.platform.action is not None
                        else ""
                    ),
                )
                if self.validate_switch(switch):
                    faction.members.remove(member)
                    target.members.append(member)
                    round_.switches.append(switch)
                    self.switches.append(switch)
                    self._emit(
                        EventType.SWITCH_RECORDED,
                        switch.model_dump(mode="json"),
                        attribution=member,
                    )
        return round_

    def _short_critique_has_evidence(self, critique: Critique) -> bool:
        """При приложенном источнике factual/logical критика должна цитировать span."""
        bundle = self.evidence
        if bundle is None or not bundle.content:
            return True
        blob = f"{critique.claim} {critique.specifics}"
        return quote_span_matches(blob, bundle.content)

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
    ) -> dict[str, object] | None:
        if target.platform is None:
            return None
        speaker = speakers.get(faction.members[(number - 1) % len(faction.members)])
        if speaker is None:
            return None
        return await self._ask(
            speaker,
            self._bind(prompt.format(
                name=faction.name,
                platform=faction.platform.thesis if faction.platform else "(none)",
                opponent=target.name, opponent_thesis=target.platform.thesis,
            )),
        )
