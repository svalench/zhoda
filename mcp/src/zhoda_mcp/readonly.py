"""Read-only граница decision-review. Нет write/execute adapters."""

from __future__ import annotations

from typing import Any

FORBIDDEN_ADAPTERS = frozenset(
    {
        "write_file",
        "apply_patch",
        "apply_migration",
        "create_issue",
        "send_message",
        "change_infra",
        "execute_shell",
        "git_push",
        "auto_merge",
        "fetch_url",
    }
)


class ReadOnlyViolation(RuntimeError):
    """Попытка side-effect из review workflow."""


def invoke_adapter(name: str, payload: dict[str, Any] | None = None) -> None:
    """Единственная точка для внешних adapters. Review её не вызывает."""
    del payload
    raise ReadOnlyViolation(
        f"read-only decision review refuses adapter {name!r}; "
        "plan is a proposal, not an execution command"
    )
