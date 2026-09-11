# @zhoda/dsh-plugin

DeepSeek Harness plugin for Zhoda: a debate room with live transcript,
an animated faction graph (watch models switch sides), and a verdict panel
with the minority report.

Design doc: [../../docs/03-dsh-plugin.md](../../docs/03-dsh-plugin.md)

## Status

Planned — after core MVP and the dsh plugin API review
(`docs/architecture.md` in `deepseek-ai/deepseek-harness`).

This directory contains design documentation only; there is no implemented or
published `@zhoda/dsh-plugin` to install. For the existing DeepSeek Harness
integration, use the [MCP server](../../mcp/README.md#deepseek-harness).

## Planned features

- Debate room: streaming transcript, faction names, cross-examinations
- Faction graph: animated public switches with the convincing argument
- Verdict panel: `zhoda_reached`, consensus strength, minority report, dissent map
- Value map view: what the council clarified before arguing
