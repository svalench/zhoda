"""Anonymization (protocol invariant): critics never see real model names.

Aliases are generated PER DELIBERATION (engine calls this inside
deliberate(), not once at init). Default seed is a hash of
question+council+context so a repeat of the same inputs can hit the debate
cache; a different question still shuffles differently — not a global map
that would de-anonymize models across sessions. Labels are A..Z, AA..AZ, ...
so councils larger than 26 models don't crash.
"""

import hashlib
import random


def _label(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA, ..."""
    label = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        label = chr(65 + rem) + label
    return label


def content_alias_seed(question: str, models: list[str], *, context: str = "") -> int:
    """Стабильный seed для повтора того же вопроса — иначе кэш дебата мёртв."""
    raw = hashlib.sha256("::".join((question, context, *models)).encode()).hexdigest()[:16]
    return int(raw, 16)


def make_aliases(models: list[str], *, seed: int | None = None) -> dict[str, str]:
    """real model id -> shuffled 'Response X' alias."""
    aliases = [f"Response {_label(i)}" for i in range(len(models))]
    rng = random.Random(seed)
    rng.shuffle(aliases)
    return dict(zip(models, aliases, strict=True))
