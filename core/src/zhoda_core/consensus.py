"""Stage 4: consensus (zhoda) detection.

Computed on structured theses, never prose similarity. Stability rule
(critique §2): consensus counts only if agreement PERSISTS for
`stability_rounds` consecutive rounds — protection against a random
majority flip.
"""

from .factions import Faction
from .models import ConsensusStrength


class ConsensusChecker:
    def __init__(self, stability_rounds: int = 2) -> None:
        self.stability_rounds = stability_rounds
        self._stable_streak = 0

    def check(self, factions: list[Faction]) -> tuple[bool, ConsensusStrength]:
        """Compare current theses; update the stability streak.
        Returns (zhoda_reached, strength). zhoda_reached=True only when
        the streak reaches stability_rounds."""
        raise NotImplementedError  # TODO(mvp)
