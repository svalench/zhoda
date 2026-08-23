"""Stage 2: faction formation.

Embeddings are a CHEAP PREFILTER ONLY (critique §3): two models can state
one position in different words (false split) or two positions in similar
words (false consensus). Final grouping is a pairwise STRUCTURAL comparison
by a cross-model ("same position? if not — what is the divergence?"); the
divergences it reports seed the dissent_map.

Factions form bottom-up from real positions — never assigned roles — to
avoid artificial polarization.
"""

from pydantic import BaseModel, Field

from .models import Position
from .providers.openrouter import OpenRouterProvider


class Faction(BaseModel):
    name: str = ""                              # named by chairman later
    members: list[str] = Field(default_factory=list)  # anonymized aliases
    platform: Position | None = None            # shared platform answer


class FactionClusterer:
    def __init__(self, provider: OpenRouterProvider, merge_threshold: float = 0.82) -> None:
        self.provider = provider
        self.merge_threshold = merge_threshold  # TODO(calibrate): bench, prefilter only

    async def cluster(self, positions: list[Position]) -> list[Faction]:
        """1) embedding prefilter on thesis+arguments (cheap candidate pairs)
        2) pairwise structural comparison for candidates (source of truth)
        3) clusters of 2+ -> factions; singletons stay independents
        """
        raise NotImplementedError  # TODO(mvp)
