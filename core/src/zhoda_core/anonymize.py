"""Anonymization (protocol invariant): critics never see real model names.

Aliases are generated per deliberation and shuffled, so position order
can't leak identity either.
"""

import random


def make_aliases(models: list[str], *, seed: int | None = None) -> dict[str, str]:
    """real model id -> 'Response A'-style alias, shuffled."""
    aliases = [f"Response {chr(65 + i)}" for i in range(len(models))]
    rng = random.Random(seed)
    rng.shuffle(aliases)
    return dict(zip(models, aliases, strict=True))
