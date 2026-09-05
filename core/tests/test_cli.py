"""CLI flags from the whitepaper: --auto-clarify / --no-clarify."""

from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

from zhoda_core.cli import app, apply_progress, format_cost_breakdown, resolve_clarify_mode
from zhoda_core.models import CostReport, Protocol
from zhoda_core.progress import ProgressEvent

# Rich красит каждый дефис отдельно: `\x1b[1;36m-\x1b[0m\x1b[1;36m-auto\x1b[0m…`
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_HELP_COLUMNS = "120"
_ABSENT_FLAG = "--not-a-zhoda-flag"


def strip_ansi(text: str) -> str:
    """Снять CSI — иначе `--auto-clarify` не находится как литерал."""
    return _ANSI_RE.sub("", text)


def normalize_help(text: str) -> str:
    """Одна строка без ANSI и переносов: флаг не зависит от ширины/цвета."""
    return re.sub(r"\s+", " ", strip_ansi(text))


def invoke_cli(args: list[str], *, color: bool, columns: str = _HELP_COLUMNS):
    """Штатный Typer runner с фиксированной шириной; .env не трогаем."""
    env: dict[str, str | None] = {
        "COLUMNS": columns,
        "TERM": "xterm-256color" if color else "dumb",
    }
    if color:
        env["FORCE_COLOR"] = "1"
        env["CLICOLOR_FORCE"] = "1"
        env["NO_COLOR"] = None
    else:
        env["NO_COLOR"] = "1"
        env["FORCE_COLOR"] = "0"
    return CliRunner().invoke(app, args, color=color, env=env)


@pytest.fixture(autouse=True)
def _skip_dotenv_on_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("zhoda_core.cli.load_zhoda_env", lambda: None)


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


def test_cli_protocol_line_is_eval_stable() -> None:
    """eval/run.sh якорится на `protocol: vote|debate|red_team`, не на traceback."""
    assert str(Protocol.VOTE) == "vote"
    assert str(Protocol.DEBATE) == "debate"
    assert str(Protocol.RED_TEAM) == "red_team"


def test_conflicting_clarify_flags_are_rejected() -> None:
    with pytest.raises(ValueError, match="only one"):
        resolve_clarify_mode(None, auto_clarify=True, no_clarify=True)
    with pytest.raises(ValueError, match="only one"):
        resolve_clarify_mode("smart", auto_clarify=True, no_clarify=False)


def test_normalize_help_recovers_ansi_split_flag() -> None:
    """Контроль хрупкости: Rich режет дефисы SGR-кодами; без нормализации assert врёт."""
    split = "\x1b[1;36m-\x1b[0m\x1b[1;36m-auto\x1b[0m\x1b[1;36m-clarify\x1b[0m"
    assert "--auto-clarify" not in split
    assert "--auto-clarify" in normalize_help(split)
    assert "--auto-clarify" not in normalize_help("--auto-cl…")
    assert _ABSENT_FLAG not in normalize_help(split)


@pytest.mark.parametrize("color", [False, True])
def test_help_lists_auto_clarify_on_deliberate_subcommand(color: bool) -> None:
    root = invoke_cli(["--help"], color=color)
    assert root.exit_code == 0, root.stdout + root.stderr
    root_text = normalize_help(root.stdout + root.stderr)
    assert "deliberate" in root_text
    assert "--auto-clarify" not in root_text

    result = invoke_cli(["deliberate", "--help"], color=color)
    assert result.exit_code == 0, result.stdout + result.stderr
    raw = result.stdout + result.stderr
    if color:
        assert "\x1b" in raw
    else:
        assert "\x1b" not in raw
    text = normalize_help(raw)
    for flag in ("--auto-clarify", "--no-clarify", "--clarify", "--context"):
        assert flag in text
    assert "open_ambiguities" in text
    assert _ABSENT_FLAG not in text


def test_auto_clarify_is_accepted_on_deliberate() -> None:
    """Опция принадлежит deliberate: нет вопроса → usage, не 'No such option'."""
    result = invoke_cli(["deliberate", "--auto-clarify"], color=False)
    combined = normalize_help(result.stdout + result.stderr)
    assert result.exit_code != 0
    assert "No such option" not in combined
    assert "Missing argument" in combined


def test_conflicting_flags_fail_before_engine() -> None:
    result = invoke_cli(
        ["deliberate", "q", "--auto-clarify", "--no-clarify"],
        color=False,
    )
    combined = normalize_help(result.stdout + result.stderr)
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


def test_breakdown_shows_cached_not_zero() -> None:
    cost = CostReport(
        requests=2,
        cache_hits=3,
        breakdown={"route": 2, "positions": 0},
        cache_breakdown={"route": 0, "positions": 3},
    )
    text = format_cost_breakdown(cost)
    assert "positions: cached" in text
    assert "route: 2" in text
    assert "positions: 0" not in text
