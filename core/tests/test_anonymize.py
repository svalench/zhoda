"""Anonymizer tests — 27+ models, per-deliberation regeneration.
Round-7 §5: no `or True` — determinism via seed, difference via two seeds.
"""

from zhoda_core.anonymize import make_aliases


def test_aliases_cover_more_than_26_models() -> None:
    models = [f"model-{i}" for i in range(30)]
    aliases = make_aliases(models)
    assert len(set(aliases.values())) == 30  # unique, no chr(65+i) crash


def test_same_seed_same_order() -> None:
    models = [f"model-{i}" for i in range(5)]
    assert make_aliases(models, seed=1) == make_aliases(models, seed=1)


def test_different_seeds_different_order() -> None:
    models = [f"model-{i}" for i in range(5)]
    first = make_aliases(models, seed=1)
    second = make_aliases(models, seed=2)
    assert set(first.values()) == set(second.values())  # same alias pool
    assert first != second  # different assignment
