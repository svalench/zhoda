"""Stage 5: verdict assembly.

The minority report is NEVER erased (protocol invariant). Dissent map is
seeded by the pairwise divergences from faction clustering.
"""

from .factions import Faction
from .models import ConsensusStrength, Protocol, ValueMap, Verdict


class VerdictBuilder:
    def build(
        self,
        factions: list[Faction],
        strength: ConsensusStrength,
        protocol: Protocol,
        value_map: ValueMap,
        *,
        router_confidence: float,
        rounds_taken: int,
        transcript_id: str,
    ) -> Verdict:
        """Majority decision + preserved minority report + dissent map."""
        raise NotImplementedError  # TODO(mvp)
