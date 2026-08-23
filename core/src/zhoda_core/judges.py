"""Judge selection with conflict-of-interest avoidance.

Two judge models from config, REQUIRED to sit outside the council (round-7
§1-2): the engine refuses to start with fewer than two clean judges. A judge
never rules on their own faction; closure requires the non-conflicted pair
to agree. There is NO silent fallback to a conflicted judge — a full
conflict is a hard error, not a soft surrender of the invariant.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .factions import Faction  # только аннотации — иначе цикл с factions.py


class JudgesConflictError(Exception):
    """All judges are conflicted for this faction — configuration error."""


class Judges:
    def __init__(self, models: tuple[str, str], aliases: dict[str, str]) -> None:
        if len(set(models)) < 2:
            raise ValueError("need two DISTINCT judge models")
        self.models = models
        self.alias_of = {m: aliases.get(m) for m in models}  # None = outside council

    def for_faction(self, faction: Faction) -> str:
        for model, alias in self.alias_of.items():
            if alias is None or alias not in faction.members:
                return model
        raise JudgesConflictError(
            f"all judges conflicted for faction {faction.name!r} — "
            "configure judges outside the council"
        )

    def pair_for(self, faction: Faction) -> tuple[str, ...]:
        clean = tuple(
            m for m, a in self.alias_of.items() if a is None or a not in faction.members
        )
        if not clean:
            raise JudgesConflictError(
                f"all judges conflicted for faction {faction.name!r} — "
                "configure judges outside the council"
            )
        return clean

    def outside(self) -> tuple[str, ...]:
        """Judges outside the council — zero possible conflict."""
        return tuple(m for m, a in self.alias_of.items() if a is None)

    def conflicts(self) -> list[str]:
        return [m for m, a in self.alias_of.items() if a is not None]
