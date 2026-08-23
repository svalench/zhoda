# Zhoda

**Models argue until they reach zhoda.**

Zhoda (from Belarusian «згода» — agreement, consensus) is an open-source deliberation engine for LLMs.

**The honest formula:** a model may not switch sides without an unclosed objection; agreement does not count until it survives two consecutive rounds; every verdict carries the minority report and the full transcript.

1. **Interviews you first** — the council asks clarifying questions only when the question is genuinely underspecified, and builds a value map (goal, success criteria, constraints, anti-goals) before answering.
2. **Forms factions** — models take positions, cluster into factions (with a real internal synthesis), and debate Oxford-style: argument → counter-argument → cross-examination → **platform revision** → faction switches. A rotating devil's advocate attacks the leading position. Consensus can be reached by convergence, not only by attrition.
3. **Reaches zhoda — or honest dissent** — you get the majority verdict, the minority report, a dissent map, and the full auditable transcript. No fake consensus.

## Why

Single models hallucinate confidently. Councils that just vote inherit shared biases. And the market is tired of synthesized "everyone agrees" — a tool that can say `zhoda_reached: false` and show the dissent map is the differentiator.

## The artifact, not the chat

A Zhoda verdict is a first-class object: decision + minority report + dissent map + who switched factions and why + the full auditable transcript. Drop it into an ADR, an RFC, or a ticket. Single-answer tools and one-pass councils don't produce this object.

## Where it plays

One vertical wedge, not "any question":

- **Architecture and product decisions inside agent harnesses** (DeepSeek Harness, Claude Code) — via MCP
- **Plan reviews in the IDE** — the agent's plan gets a factional review before execution

Explicitly not: medicine, finance, CISO theater — regulated domains would eat the project.

Runs on OpenRouter with BYOK — `:free` or cheap paid, hard per-question budget.

## What works / what doesn't

**Works today** (core/, Python 3.12+):

- Full deliberation loop: protocol router (two classifiers) → smart elicitation → positions → faction synthesis → debate rounds with platform revision → consensus with stability rule → verdict + minority report + transcript
- CLI: `zhoda deliberate "..."` (interactive clarifying questions)
- Conflict-free judging: two judges outside the council, pairwise closure votes
- Honest provider: per-question budget with pre-call estimate, 429/quota split, sqlite cache
- Test suite: provider gates, ledger gates, scripted e2e (revision / stability flip / deadlock / smart degradation / state isolation)

**Doesn't exist yet:** MCP server, dsh plugin, FastAPI server, escalation ladder, reputation. They land only after the core is green on live models.

## Quickstart

```bash
cd core
uv sync
cp zhoda.yaml.example zhoda.yaml   # set council + judges (outside the council!)
# .env in repo root or core/ — no export needed
uv run zhoda deliberate "Monolith or microservices for a 4-person B2B SaaS MVP?"
uv run pytest -m "not live"          # test suite, no network
```

## Architecture (3 layers)

| Layer | Package | What it is |
|---|---|---|
| Core | `zhoda-core` | FastAPI deliberation engine: protocol router, elicitation, factions, debate rounds, consensus, verdicts, reputation |
| MCP | `zhoda-mcp` | Model Context Protocol server — works in DeepSeek Harness, Claude Code, Codex |
| Plugin | `@zhoda/dsh-plugin` | DeepSeek Harness plugin: debate room UI, faction graph, verdict panel |

## Status

Early. See [docs/](docs/):

- [Whitepaper](docs/whitepaper.md) — the Zhoda protocol (EN)
- [Master plan](docs/master-plan.md) — vision, roadmap, funding model
- [01: Core](docs/01-core.md) — deliberation engine design
- [02: MCP server](docs/02-mcp-server.md) — cross-harness distribution
- [03: dsh plugin](docs/03-dsh-plugin.md) — DeepSeek Harness showcase
- [04: Critique response](docs/04-critique-response.md) — design review resolutions (RU)

## Links

- Site: https://zhoda.dev (soon)

## License

Apache-2.0 (core engine, MCP server, plugins) · AGPL-3.0 (API server) — see [LICENSE.md](LICENSE.md)
