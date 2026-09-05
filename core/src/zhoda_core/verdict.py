"""Stage 5: verdict assembly.

The minority report is NEVER erased (protocol invariant). The dissent map is
seeded by the pairwise divergences from faction clustering. The plan
contract, decision tree and dead-ends metric are attached by the engine
(values №1–№3).
"""

from .factions import Faction
from .guards import (
    challenges_loaded_premise,
    ensure_claims_in_decision,
    ensure_loaded_premise_not_adopted,
    is_hedge_text,
    is_hybrid_decision,
    looks_like_loaded_premise,
    looks_like_xor_question,
)
from .models import (
    ConsensusStrength,
    CostReport,
    Critique,
    Disagreement,
    FactionSwitch,
    ObjectionStatus,
    Protocol,
    ValueMap,
    Verdict,
    bind_user_context,
)
from .providers.openrouter import OpenRouterProvider, make_cache_key

SYNTHETIC_LABEL = (
    "[synthetic opposition — no council model held this position]"
)


class VerdictBuilder:
    def build(
        self,
        factions: list[Faction],
        strength: ConsensusStrength,
        protocol: Protocol,
        value_map: ValueMap,
        *,
        zhoda_reached: bool,
        router_confidence: float,
        rounds_taken: int,
        transcript_id: str,
        switches: list[FactionSwitch],
        cost: CostReport,
        divergences: list[Disagreement],
        question: str = "",
    ) -> Verdict:
        leading = max(factions, key=lambda f: len(f.members))
        others = [f for f in factions if f is not leading and f.platform]
        # UNANIMOUS: судьи уже сказали «одна позиция» — minority не дублирует её
        minority_report = None
        if strength != ConsensusStrength.UNANIMOUS:
            minority_report = (
                "\n\n".join(
                    _minority_line(f) for f in others if f.platform is not None
                )
                or None
            )
        if not zhoda_reached or strength in (
            ConsensusStrength.SPLIT,
            ConsensusStrength.DEADLOCK,
        ):
            decision = _dissent_decision(strength, factions, question=question)
        else:
            # черновик: thesis, не сырой answer платформы (тот — текст для спора)
            decision = leading.platform.thesis if leading.platform else ""
        return Verdict(
            decision=decision,
            zhoda_reached=zhoda_reached,
            consensus_strength=strength,
            protocol=protocol,
            router_confidence=router_confidence,
            value_map=value_map,
            minority_report=minority_report,
            dissent_map=divergences,
            switches=switches,
            rounds_taken=rounds_taken,
            cost=cost,
            transcript_id=transcript_id,
        )


def _minority_line(faction: Faction) -> str:
    """Синтетическая оппозиция помечается — иначе minority врёт о диссенте."""
    thesis = faction.platform.thesis if faction.platform is not None else ""
    if faction.synthetic:
        return f"{faction.name} {SYNTHETIC_LABEL}: {thesis}"
    return f"{faction.name}: {thesis}"


def _dissent_decision(
    strength: ConsensusStrength,
    factions: list[Faction],
    *,
    question: str = "",
) -> str:
    """Split/deadlock — карта тезисов. Majority без zhoda — действие лидера,
    явно не zhoda, затем dissent. Сырой platform.answer не выдаём за решение.
    Loaded premise в rec не принимается (протокол, не minority thesis).
    """
    if strength is ConsensusStrength.MAJORITY:
        leading = max(factions, key=lambda f: len(f.members))
        thesis = leading.platform.thesis if leading.platform is not None else ""
        thesis = ensure_loaded_premise_not_adopted(question, thesis)
        lines = [
            f"Recommended (majority at cap, not zhoda): {thesis}",
            "Dissent:",
        ]
        for faction in factions:
            if faction is leading:
                continue
            lines.append(_minority_line(faction))
        if len(lines) == 2:
            lines.append("(none)")
        return "\n".join(lines)
    lines = [f"No zhoda ({strength.value})."]
    for faction in factions:
        lines.append(_minority_line(faction))
    return "\n".join(lines)


DECISION_PROMPT = """SYNTHESIZE THE COUNCIL DECISION for the user — not a debate rebuttal.
First sentence: restate the winner's primary recommended action (same pick as
Winner thesis). Do NOT open with "it depends", "both are comparable", or
"choose based on team expertise". If the question is A or B / A vs B, name
ONE primary recommendation — do not synthesize a hybrid that adopts both
options as the action. Caveats and overturn conditions come AFTER the pick.
Do not blend the minority into the action.
EXCEPTION: assertions in the user question are not confirmed constraints.
If the question embeds a loaded premise (always/never/since/given that/why is),
evaluate whether that premise is true. Do not adopt a false premise or answer
a loaded "why" as if the assertion were a fact — even if Winner thesis
explained it. Reject the premise; that rejection is the primary rec.
Then: why, which objections were closed, conditions that would overturn this.
Do NOT write in first person as a faction (no "we", "our platform").
Do NOT treat unresolved ambiguities as confirmed facts — list them as unresolved.
Do NOT describe a superseded objection as a disproven finding: the winner
revised the thesis; concrete Winner claims still stand unless they appear
under Closed objections (refuted by rebuttal).
Keep Winner claims in the decision when they are the reasons for the action.
A concrete finding in Winner claims (SQL injection, interpolation, plaintext)
MUST appear in the decision. Open ambiguities ("maybe the driver sanitizes")
do not erase those claims — name the finding, then the uncertainty.

Question: {question}
Winner thesis: {thesis}
Winner claims (keep unless refuted): {claims}
Winner platform (debate text, context only): {answer}
Closed objections (refuted by rebuttal): {closed}
Platform revisions (objection addressed by changing the thesis; the finding may still stand): {revised}
Open objections against the winner: {open_objections}
Overturn if (falsifiability): {falsifiability}
Confirmed constraints: {constraints}
Unresolved (NOT facts): {open_ambiguities}

ONLY valid JSON: {{"decision": "..."}}"""


def partition_objections_for_decision(
    objections: list[Critique],
    winner_name: str,
) -> tuple[list[str], list[str], list[str]]:
    """CLOSED = опровергнуты; SUPERSEDED = ревизия тезиса, находка может остаться."""
    closed = [c.claim for c in objections if c.status is ObjectionStatus.CLOSED]
    revised = [c.claim for c in objections if c.status is ObjectionStatus.SUPERSEDED]
    open_against = [
        c.claim
        for c in objections
        if c.status is ObjectionStatus.OPEN and c.target_faction == winner_name
    ]
    return closed, revised, open_against


async def synthesize_decision(
    provider: OpenRouterProvider,
    chairman: str,
    *,
    question: str,
    leading: Faction,
    objections: list[Critique],
    value_map: ValueMap,
) -> str:
    """Председатель пишет решение пользователю. Сбой парсинга — на стороне engine."""
    if leading.platform is None:
        return ""
    closed, revised, open_against = partition_objections_for_decision(
        objections, leading.name
    )
    claims = "; ".join(c.claim for c in leading.platform.claims) or "(none)"
    prompt = bind_user_context(
        DECISION_PROMPT.format(
            question=question,
            thesis=leading.platform.thesis,
            claims=claims,
            answer=leading.platform.answer,
            closed="; ".join(closed) or "(none)",
            revised="; ".join(revised) or "(none)",
            open_objections="; ".join(open_against) or "(none)",
            falsifiability=leading.platform.falsifiability,
            constraints="; ".join(value_map.constraints) or "(none)",
            open_ambiguities="; ".join(value_map.open_ambiguities) or "(none)",
        ),
        value_map.as_prompt_block(),
    )
    data = await provider.ask_json(
        chairman,
        prompt,
        cache_key=make_cache_key("decision", chairman, prompt),
    )
    decision = str(data.get("decision") or "").strip()
    if not decision:
        raise ValueError("empty synthesized decision")
    thesis = leading.platform.thesis
    if is_hedge_text(decision) and not is_hedge_text(thesis):
        decision = thesis
    elif (
        looks_like_xor_question(question)
        and is_hybrid_decision(decision)
        and not is_hybrid_decision(thesis)
    ):
        decision = thesis
    elif looks_like_loaded_premise(question) and not challenges_loaded_premise(decision):
        # Председатель не принимает premise; если победитель отверг — его thesis.
        if challenges_loaded_premise(thesis):
            decision = thesis
        else:
            decision = ensure_loaded_premise_not_adopted(question, decision)
    claim_texts = [c.claim for c in leading.platform.claims]
    return ensure_claims_in_decision(decision, claim_texts)
