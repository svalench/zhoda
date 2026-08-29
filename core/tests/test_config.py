"""Сборка engine из YAML — общая для CLI и MCP."""

from pathlib import Path

import pytest

from zhoda_core.config import load_council_config, make_engine
from zhoda_core.providers.openrouter import OpenRouterProvider

EXAMPLE = Path(__file__).resolve().parents[1] / "zhoda.yaml.example"


def test_load_example_council_config() -> None:
    cfg = load_council_config(EXAMPLE)
    assert len(cfg["council"]) >= 2
    assert len(cfg["judges"]) >= 2
    assert len(set(cfg["router_classifiers"])) >= 2


def test_make_engine_requires_judges() -> None:
    cfg = {
        "council": ["a", "b"],
        "router_classifiers": ["c", "d"],
    }
    with pytest.raises(ValueError, match="no judges"):
        make_engine(cfg, OpenRouterProvider(api_key="test"))


def test_make_engine_requires_two_classifiers() -> None:
    cfg = {
        "council": ["a", "b"],
        "judges": ["c", "d"],
        "router_classifiers": ["c"],
    }
    with pytest.raises(ValueError, match="router_classifiers"):
        make_engine(cfg, OpenRouterProvider(api_key="test"))
