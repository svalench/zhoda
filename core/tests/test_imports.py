"""Импорт CLI не должен падать на цикле factions ↔ judges."""


def test_cli_imports_without_circular_cycle() -> None:
    from zhoda_core.cli import app
    from zhoda_core.engine import ZhodaEngine
    from zhoda_core.factions import Faction
    from zhoda_core.judges import Judges

    assert app is not None
    assert ZhodaEngine is not None
    assert Faction.__name__ == "Faction"
    assert Judges.__name__ == "Judges"
