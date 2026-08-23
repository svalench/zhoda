"""The second render (values №2) and the ROI metric (values №3).

A Zhoda verdict renders twice: a narrative for humans, and a PLAN CONTRACT
for a cheaper executor model — step-by-step tickets with explicit assumptions
instead of context, because the executor is dumber and nothing may be left
to inference. Rejected paths are collected PROGRAMMATICALLY from the
objection ledger and the minority — never invented by the rendering model.
"""

from .factions import Faction
from .models import (
    Critique,
    ObjectionStatus,
    PlanContract,
    RejectedPath,
    Verdict,
)
from .providers.openrouter import OpenRouterProvider, make_cache_key

PLAN_PROMPT = """Render this deliberation outcome as a PLAN CONTRACT for a cheaper
executor model. The executor is dumber than the council: every step needs its
goal, hard constraints, forbidden paths, and an acceptance criterion.

Decision: {decision}
Value map: {value_map}
Rejected paths (do NOT invent more): {rejected}
Open ambiguities: {ambiguities}

ONLY valid JSON:
{{"goal": "...",
  "steps": [{{"step": "...", "goal": "...",
             "hard_constraints": ["..."], "forbidden_paths": ["..."],
             "acceptance": "..."}}],
  "constraints": ["global hard constraint"],
  "open_ambiguities": ["..."]}}"""


def collect_rejected_paths(
    factions: list[Faction],
    objections: list[Critique],
    leading: Faction,
) -> list[RejectedPath]:
    """What the council rejected and why — the dead ends a cheap executor
    must NOT walk into again (values №3). Programmatic, auditable."""
    paths: list[RejectedPath] = []
    for faction in factions:
        if faction is not leading and faction.platform is not None:
            paths.append(RejectedPath(
                path=faction.platform.thesis,
                rejected_by="majority",
                why="minority position after the debate",
            ))
    for critique in objections:
        if (
            critique.status == ObjectionStatus.OPEN
            and critique.target_faction == leading.name
        ):
            paths.append(RejectedPath(
                path=critique.claim,
                rejected_by=critique.author_faction or "council",
                why="unclosed objection against the winning platform",
            ))
    return paths


async def render_plan_contract(
    provider: OpenRouterProvider,
    chairman: str,
    verdict: Verdict,
    objections: list[Critique],
    factions: list[Faction],
) -> PlanContract:
    """Chairman renders steps/constraints; rejected_paths and open_ambiguities
    are overwritten programmatically — the model writes prose, the protocol
    owns the facts."""
    leading = max(factions, key=lambda f: len(f.members))
    rejected = collect_rejected_paths(factions, objections, leading)
    data = await provider.ask_json(
        chairman,
        PLAN_PROMPT.format(
            decision=verdict.decision,
            value_map=verdict.value_map.model_dump(),
            rejected=[p.model_dump() for p in rejected],
            ambiguities=verdict.value_map.open_ambiguities,
        ),
        cache_key=make_cache_key("plan", verdict.transcript_id),
    )
    contract = PlanContract(**data)
    contract.rejected_paths = rejected
    contract.open_ambiguities = verdict.value_map.open_ambiguities
    return contract
