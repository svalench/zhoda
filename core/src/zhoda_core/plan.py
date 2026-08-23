"""The second render (values №2) — gated on zhoda (round-10 §2).

`paths_rejected` (round-10 §3, widened in round-11 §1): an honest
programmatic count of what a REACHED consensus rejected — minority positions
that lost the vote AND objections that stayed open against the winning
platform (the council chose the path despite the known flaw: the unaddressed
version of the path is what got rejected). At split/deadlock nothing was
rejected — an unresolved dispute is not a rejection.
"""

from .factions import Faction
from .models import Critique, ObjectionStatus, PlanContract, RejectedPath, Verdict
from .providers.openrouter import OpenRouterProvider, make_cache_key

PLAN_PROMPT = """Render this deliberation outcome as a PLAN CONTRACT for a cheaper
executor model. The executor is dumber than the council: every step needs its
goal, hard constraints, forbidden paths, and an acceptance criterion.

Decision: {decision}
Value map: {value_map}
Rejected paths (do NOT invent more): {rejected}
Open ambiguities (carry them as explicit constraints): {ambiguities}

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
    *,
    zhoda_reached: bool,
) -> list[RejectedPath]:
    """What a REACHED consensus rejected (round-11 §1 — both sources):
    minority positions that lost the vote, and objections that stayed open
    against the winning platform (an accepted weakness: the unaddressed
    version of the chosen path is what got rejected)."""
    if not zhoda_reached:
        return []
    leading = max(factions, key=lambda f: len(f.members))
    paths = [
        RejectedPath(
            path=f.platform.thesis,
            rejected_by="majority",
            why="minority position after a reached consensus",
        )
        for f in factions
        if f is not leading and f.platform is not None
    ]
    paths += [
        RejectedPath(
            path=c.claim,
            rejected_by=c.author_faction or "council",
            why="accepted weakness — unclosed objection against the chosen platform",
        )
        for c in objections
        if c.status == ObjectionStatus.OPEN and c.target_faction == leading.name
    ]
    return paths


async def render_plan_contract(
    provider: OpenRouterProvider,
    chairman: str,
    verdict: Verdict,
) -> PlanContract:
    """Chairman renders steps/constraints; rejected_paths and open_ambiguities
    are overwritten programmatically — the model writes prose, the protocol
    owns the facts."""
    data = await provider.ask_json(
        chairman,
        PLAN_PROMPT.format(
            decision=verdict.decision,
            value_map=verdict.value_map.model_dump(),
            rejected=[p.model_dump() for p in verdict.paths_rejected],
            ambiguities=verdict.value_map.open_ambiguities,
        ),
        cache_key=make_cache_key("plan", verdict.transcript_id),
    )
    contract = PlanContract(**data)
    contract.rejected_paths = verdict.paths_rejected
    contract.open_ambiguities = verdict.value_map.open_ambiguities
    return contract
