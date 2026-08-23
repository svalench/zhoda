"""Ближайший .env подхватывается без export и без uv --env-file."""

import os

from zhoda_core.env import load_zhoda_env
from zhoda_core.providers.openrouter import OpenRouterProvider


def test_dotenv_walks_up_to_git_root(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=from-root\n", encoding="utf-8")
    nested = tmp_path / "core"
    nested.mkdir()
    monkeypatch.chdir(nested)
    assert load_zhoda_env() == tmp_path / ".env"
    assert os.environ["OPENROUTER_API_KEY"] == "from-root"


def test_dotenv_does_not_override_existing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-shell")
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    load_zhoda_env()
    assert os.environ["OPENROUTER_API_KEY"] == "from-shell"


def test_dotenv_does_not_walk_past_git_root(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=from-home\n", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    nested = repo / "core"
    nested.mkdir()
    monkeypatch.chdir(nested)
    assert load_zhoda_env() is None
    assert "OPENROUTER_API_KEY" not in os.environ


def test_provider_reads_dotenv_when_key_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    (tmp_path / ".env").write_text('export OPENROUTER_API_KEY="sk-from-file"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    provider = OpenRouterProvider()
    assert provider.api_key == "sk-from-file"
