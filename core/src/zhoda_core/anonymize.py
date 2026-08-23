"""Anonymization (protocol invariant): critics never see real model names.

Round-4 §7: aliases are generated PER DELIBERATION (engine calls this inside
deliberate(), not once at init) — a stable mapping across sessions would
allow statistical de-anonymization. Labels are A..Z, AA..AZ, ... so councils
larger than 26 models don't crash.
"""

import random


def _label(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA, ..."""
    label = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        label = chr(65 + rem) + label
    return label


def make_aliases(models: list[str], *, seed: int | None = None) -> dict[str, str]:
    """real model id -> shuffled 'Response X' alias."""
    aliases = [f"Response {_label(i)}" for i in range(len(models))]
    rng = random.Random(seed)
    rng.shuffle(aliases)
    return dict(zip(models, aliases, strict=True))
