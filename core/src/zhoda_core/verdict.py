"""Stage 5: verdict assembly.

The minority report is NEVER erased (protocol invariant). The dissent map is
seeded by the pairwise divergences from faction clustering. The plan
contract, decision tree and dead-ends metric are attached by the engine
(values №1–№3).
"""

from .factions import Faction
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
from .providers.openrouter import OpenRouterProvider

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
        if strength in (ConsensusStrength.SPLIT, ConsensusStrength.DEADLOCK):
            decision = _dissent_decision(strength, factions)
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


def _dissent_decision(strength: ConsensusStrength, factions: list[Faction]) -> str:
    """Карта разногласий: answer лидера не выдаём за решение совета."""
    lines = [f"No zhoda ({strength.value})."]
    for faction in factions:
        lines.append(_minority_line(faction))
    return "\n".join(lines)


DECISION_PROMPT = """SYNTHESIZE THE COUNCIL DECISION for the user — not a debate rebuttal.
First line: the recommended action.
Then: why, which objections were closed, conditions that would overturn this.
Do NOT write in first person as a faction (no "we", "our platform").
Do NOT treat unresolved ambiguities as confirmed facts — list them as unresolved.

Question: {question}
Winner thesis: {thesis}
Winner platform (debate text, context only): {answer}
Closed objections: {closed}
Open objections against the winner: {open_objections}
Overturn if (falsifiability): {falsifiability}
Confirmed constraints: {constraints}
Unresolved (NOT facts): {open_ambiguities}

ONLY valid JSON: {{"decision": "..."}}"""


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
    closed = [
        c.claim
        for c in objections
        if c.status in (ObjectionStatus.CLOSED, ObjectionStatus.SUPERSEDED)
    ]
    open_against = [
        c.claim
        for c in objections
        if c.status == ObjectionStatus.OPEN and c.target_faction == leading.name
    ]
    data = await provider.ask_json(
        chairman,
        bind_user_context(
            DECISION_PROMPT.format(
                question=question,
                thesis=leading.platform.thesis,
                answer=leading.platform.answer,
                closed="; ".join(closed) or "(none)",
                open_objections="; ".join(open_against) or "(none)",
                falsifiability=leading.platform.falsifiability,
                constraints="; ".join(value_map.constraints) or "(none)",
                open_ambiguities="; ".join(value_map.open_ambiguities) or "(none)",
            ),
            value_map.as_prompt_block(),
        ),
    )
    decision = str(data.get("decision") or "").strip()
    if not decision:
        raise ValueError("empty synthesized decision")
    return decision
