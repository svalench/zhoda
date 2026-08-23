"""CLI flags from the whitepaper: --auto-clarify / --no-clarify."""

import pytest
from typer.testing import CliRunner

from zhoda_core.cli import app, apply_progress, resolve_clarify_mode
from zhoda_core.progress import ProgressEvent


def test_auto_clarify_flag_resolves() -> None:
    assert resolve_clarify_mode(None, auto_clarify=True, no_clarify=False) == "auto-clarify"
    assert resolve_clarify_mode(None, auto_clarify=False, no_clarify=True) == "no-clarify"
    assert resolve_clarify_mode(None, auto_clarify=False, no_clarify=False) == "smart"
    assert (
        resolve_clarify_mode(
            "auto-clarify",
            auto_clarify=False,
            no_clarify=False,
        )
        == "auto-clarify"
    )


def test_conflicting_clarify_flags_are_rejected() -> None:
    with pytest.raises(ValueError, match="only one"):
        resolve_clarify_mode(None, auto_clarify=True, no_clarify=True)
    with pytest.raises(ValueError, match="only one"):
        resolve_clarify_mode("smart", auto_clarify=True, no_clarify=False)


def test_help_lists_auto_clarify_on_deliberate_subcommand() -> None:
    root = CliRunner().invoke(app, ["--help"])
    assert root.exit_code == 0, root.stdout
    assert "deliberate" in root.stdout

    result = CliRunner().invoke(app, ["deliberate", "--help"])
    assert result.exit_code == 0, result.stdout
    assert "--auto-clarify" in result.stdout
    assert "--no-clarify" in result.stdout
    assert "--clarify" in result.stdout
    assert "--context" in result.stdout


def test_conflicting_flags_fail_before_engine() -> None:
    result = CliRunner().invoke(
        app,
        ["deliberate", "q", "--auto-clarify", "--no-clarify"],
    )
    combined = result.stdout + result.stderr
    assert result.exit_code != 0
    assert "No such option" not in combined
    assert "only one" in combined


class _FakeStatus:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def update(self, message: str) -> None:
        self.messages.append(message)


def test_apply_progress_prints_done_and_updates_spinner() -> None:
    status = _FakeStatus()
    apply_progress(ProgressEvent("route", "routing protocol…"), status)
    apply_progress(ProgressEvent("route", "protocol=debate", done=True), status)
    assert status.messages[-1] == "[cyan]protocol=debate[/cyan]"
    assert status.messages[0] == "[cyan]routing protocol…[/cyan]"
