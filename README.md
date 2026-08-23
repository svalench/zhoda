# Zhoda

**Models argue until they reach zhoda.**

Zhoda (from Belarusian «згода» — agreement, consensus) is an open-source deliberation engine for LLMs. Instead of asking one model and hoping it is right, Zhoda:

1. **Interviews you first** — the council asks clarifying questions only when the question is genuinely underspecified, and builds a value map (goal, success criteria, constraints, anti-goals) before answering.
2. **Forms factions** — models take positions, cluster into factions, and debate Oxford-style: argument → counter-argument → cross-examination. A rotating devil's advocate attacks the leading position. Models switch factions only when their rebuttal fails — the transcript shows who moved and why.
3. **Reaches zhoda — or honest dissent** — you get the majority verdict, the minority report, a dissent map, and the full auditable transcript. No fake consensus.

## Why

Single models hallucinate confidently. Councils that just vote inherit shared biases. Zhoda makes models *argue* — the way humans actually get to the truth.

Beachhead use case: **architecture decision verdicts for coding agents** — ADR reviews, "refactor X or Y", library choices. Measurable, and the audience already lives in agent harnesses.

Runs on free OpenRouter `:free` models with BYOK. Your keys, your budget cap.

## Architecture (3 layers)

| Layer | Package | What it is |
|---|---|---|
| Core | `zhoda-core` | FastAPI deliberation engine: protocol router, elicitation, factions, debate rounds, consensus, verdicts, reputation |
| MCP | `zhoda-mcp` | Model Context Protocol server — works in DeepSeek Harness, Claude Code, Codex |
| Plugin | `@zhoda/dsh-plugin` | DeepSeek Harness plugin: debate room UI, faction graph, verdict panel |

## Status

Early design stage. See [docs/](docs/):

- [Whitepaper](docs/whitepaper.md) — the Zhoda protocol (EN)
- [Master plan](docs/master-plan.md) — vision, roadmap, funding model
- [01: Core](docs/01-core.md) — deliberation engine design
- [02: MCP server](docs/02-mcp-server.md) — cross-harness distribution
- [03: dsh plugin](docs/03-dsh-plugin.md) — DeepSeek Harness showcase
- [04: Critique response](docs/04-critique-response.md) — design review resolutions (RU)

## Links

- Site: https://zhoda.dev (soon)

## License

Apache-2.0 (core engine, MCP server, plugins) · AGPL-3.0 (API server)
