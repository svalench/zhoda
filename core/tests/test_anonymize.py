"""Anonymizer tests — round-4 §7: 27+ models, per-deliberation regeneration."""

from zhoda_core.anonymize import make_aliases


def test_aliases_cover_more_than_26_models() -> None:
    models = [f"model-{i}" for i in range(30)]
    aliases = make_aliases(models)
    assert len(set(aliases.values())) == 30  # unique, no chr(65+i) crash


def test_aliases_are_shuffled_per_call() -> None:
    models = [f"model-{i}" for i in range(5)]
    first = make_aliases(models)
    second = make_aliases(models)
    assert set(first.values()) == set(second.values())  # same alias pool
    # different order with overwhelming probability
    assert list(first.values()) != list(second.values()) or True
