"""Имена инструментов совпадают с docs/02-mcp-server.md."""

from zhoda_mcp.server import mcp

EXPECTED = {
    "zhoda_clarify",
    "zhoda_deliberate",
    "zhoda_verdict",
    "zhoda_transcript",
    "zhoda_reputation",
}


def test_five_tools_registered() -> None:
    names = set(mcp._tool_manager._tools)
    assert names == EXPECTED
