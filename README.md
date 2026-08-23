# Zhoda

**Models argue until they reach zhoda.**

Zhoda (from Belarusian «згода» — agreement, consensus) is an open-source deliberation engine for LLMs. Instead of asking one model and hoping it is right, Zhoda:

1. **Interviews you first** — the council asks 2–4 clarifying questions and builds a value map (goal, success criteria, constraints, anti-goals) before answering anything.
2. **Forms factions** — models take positions, cluster into factions, and debate Oxford-style: argument → counter-argument → cross-examination. A rotating devil's advocate attacks the leading position. Models may publicly switch factions when convinced — the transcript shows who moved and why.
3. **Reaches zhoda — or honest dissent** — you get the majority verdict, the minority report, a dissent map, and the full auditable transcript. No fake consensus.

## Why

Single models hallucinate confidently. Councils that just vote inherit shared biases. Zhoda makes models *argue* — the way humans actually get to the truth.

Runs on free OpenRouter `:free` models with BYOK. Your keys, your budget cap.

## Architecture (3 layers)

| Layer | Package | What it is |
|---|---|---|
| Core | `zhoda-core` | FastAPI deliberation engine: elicitation, factions, debate rounds, consensus, verdicts, reputation |
| MCP | `zhoda-mcp` | Model Context Protocol server — works in DeepSeek Harness, Claude Code, Codex |
| Plugin | `@zhoda/dsh-plugin` | DeepSeek Harness plugin: debate room UI, faction graph, verdict panel |

## Status

Early design stage. See [docs/](docs/):

- [Master plan](docs/master-plan.md) — vision, roadmap, funding model
- [01: Core](docs/01-core.md) — deliberation engine design
- [02: MCP server](docs/02-mcp-server.md) — cross-harness distribution
- [03: dsh plugin](docs/03-dsh-plugin.md) — DeepSeek Harness showcase

## Links

- Site: https://zhoda.dev (soon)
- Community & donations: https://zhoda.org (soon)

## License

AGPL-3.0
