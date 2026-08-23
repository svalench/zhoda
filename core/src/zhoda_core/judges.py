"""Judge selection with conflict-of-interest avoidance (round-5 §2, round-6 §5).

Two judge models come from config (recommended: OUTSIDE the council — then
`conflicts()` is empty and `outside()` is the full pair). A judge never rules
on anything involving their own faction; closure requires the whole
non-conflicted pair to agree — disagreement leaves the objection OPEN.
"""

from .factions import Faction


class Judges:
    def __init__(self, models: tuple[str, str], aliases: dict[str, str]) -> None:
        if len(set(models)) < 2:
            raise ValueError("need two DISTINCT judge models")
        self.models = models
        # alias is None for judges outside the council — they never conflict
        self.alias_of = {m: aliases.get(m) for m in models}

    def for_faction(self, faction: Faction) -> str:
        """A judge that is not a member of this faction."""
        for model, alias in self.alias_of.items():
            if alias is None or alias not in faction.members:
                return model
        return self.models[0]  # both conflicted — config should avoid this

    def pair_for(self, faction: Faction) -> tuple[str, ...]:
        """All judges not conflicted against this faction (for closure votes)."""
        clean = tuple(
            m for m, a in self.alias_of.items() if a is None or a not in faction.members
        )
        return clean or (self.models[0],)

    def outside(self) -> tuple[str, ...]:
        """Judges outside the council — zero possible conflict (consensus probe)."""
        return tuple(m for m, a in self.alias_of.items() if a is None)

    def conflicts(self) -> list[str]:
        """Judge models that sit in the council — the CLI warns about these."""
        return [m for m, a in self.alias_of.items() if a is not None]
