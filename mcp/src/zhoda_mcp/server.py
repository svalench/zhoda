"""FastMCP-сервер: stdio по умолчанию, SSE через ZHODA_MCP_TRANSPORT=sse."""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from zhoda_core.env import load_zhoda_env

from .runtime import Runtime, progress_sink

mcp = FastMCP("zhoda")
_runtime: Runtime | None = None


def get_runtime() -> Runtime:
    global _runtime
    if _runtime is None:
        _runtime = Runtime.from_env()
    return _runtime


def set_runtime(runtime: Runtime | None) -> None:
    global _runtime
    _runtime = runtime


@mcp.tool()
async def zhoda_clarify(question: str, context: str = "") -> dict[str, Any]:
    """Stage 0 only: clarifying questions plus a cost/time estimate for the full cycle."""
    return await get_runtime().clarify(question, context=context)


@mcp.tool()
async def zhoda_deliberate(
    question: str,
    confirm: bool = False,
    value_map: dict[str, Any] | None = None,
    rounds_cap: int | None = None,
    protocol: str | None = None,
    context: str = "",
    ctx: Context | None = None,  # type: ignore[type-arg]
) -> dict[str, Any]:
    """Full deliberation. First call with confirm=false returns an estimate; confirm=true runs it."""
    return await get_runtime().deliberate(
        question,
        confirm=confirm,
        value_map=value_map,
        rounds_cap=rounds_cap,
        protocol=protocol,
        context=context,
        on_progress=progress_sink(ctx),
    )


@mcp.tool()
async def zhoda_verdict(transcript_id: str) -> dict[str, Any]:
    """Re-read a stored verdict by transcript id. Does not re-run the council."""
    return get_runtime().verdict(transcript_id)


@mcp.tool()
async def zhoda_transcript(transcript_id: str, format: str = "json") -> dict[str, Any]:
    """Full debate transcript (хроніка) as json events or markdown."""
    return get_runtime().transcript(transcript_id, fmt=format)


@mcp.tool()
async def zhoda_reputation(domain: str | None = None) -> dict[str, Any]:
    """Per-domain model ratings. Omit domain for the full matrix."""
    return get_runtime().reputation_report(domain)


def main() -> None:
    load_zhoda_env()
    transport = os.environ.get("ZHODA_MCP_TRANSPORT", "stdio")
    if transport == "sse":
        mcp.run(transport="sse")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
