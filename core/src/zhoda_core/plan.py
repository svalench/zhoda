"""The second render (values №2) — gated on zhoda (round-10 §2).

A plan contract is rendered ONLY when consensus was reached: a spec built on
'we did not decide' would hand the cheap executor a document founded on
dissent, with the dissenters' positions written into its forbidden paths.

`paths_rejected` is an honest programmatic count (round-10 §3): minority
positions rejected by a REACHED consensus. It measures rejections, not
prevented dead ends — the counterfactual ROI metric waits for executor
feedback.
"""

from .factions import Faction
from .models import PlanContract, RejectedPath, Verdict
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
    *,
    zhoda_reached: bool,
) -> list[RejectedPath]:
    """Minority positions rejected by a REACHED consensus. At split/deadlock
    nothing was rejected — an unresolved dispute is not a rejection, and it
    is not counted (round-10 §2)."""
    if not zhoda_reached:
        return []
    leading = max(factions, key=lambda f: len(f.members))
    return [
        RejectedPath(
            path=faction.platform.thesis,
            rejected_by="majority",
            why="minority position after a reached consensus",
        )
        for faction in factions
        if faction is not leading and faction.platform is not None
    ]


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
