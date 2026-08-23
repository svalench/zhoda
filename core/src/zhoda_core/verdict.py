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
    Disagreement,
    FactionSwitch,
    Protocol,
    ValueMap,
    Verdict,
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
        minority_report = (
            "\n\n".join(f"{f.name}: {f.platform.thesis}" for f in others) or None
        )
        return Verdict(
            decision=leading.platform.answer if leading.platform else "",
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
