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

**Works today (mcp/):** stdio MCP server — `zhoda_clarify` / `zhoda_deliberate` (estimate, then `confirm=true`) / `zhoda_verdict` / `zhoda_transcript` / `zhoda_review` (read-only decision review) / `zhoda_reputation`. In-process core. Cursor: `.cursor/mcp.json`. DeepSeek Harness: patch in `mcp/examples/dsh.cordis.patch.yml` (not `mcpServers` JSON). See [mcp/README.md](mcp/README.md).

**Doesn't exist yet:** dsh plugin, FastAPI hosted server, PyPI/MCP-registry publish. Live ELO updates after each verdict are still a follow-up.

## Quickstart

**Prerequisites:** Python 3.12+, `uv`, and a source checkout of this repository.
`zhoda-core` and `zhoda-mcp` are **not published to PyPI yet**. There are two
separate uv projects, `core/` and `mcp/`, not a project at the repository root.
Run all commands below from the repository root.

### 1. Install and test without an API key

```bash
uv --directory core sync --python 3.12 --locked
uv --directory core run zhoda --help
uv --directory core run pytest -m "not live"
```

The initial sync may download dependencies; the help command and non-live tests
make no model API calls. No `.env`, council YAML, or API key is needed.
Keep `-m "not live"` when testing core: a bare `pytest` can select the live
OpenRouter smoke test if a key is available.
See [core/README.md](core/README.md) for the source layout and CI checks.

### 2. Try the offline review demo

```bash
uv --directory mcp sync --python 3.12 --locked
uv --directory mcp run zhoda-review-demo
```

This uses a **scripted engine**, not live models, and reads the sample ADR and
plan in [mcp/examples/review/](mcp/examples/review/). It prints `zhoda.review.v1`
JSON with `approved: false` and `demo.live: false`. It needs no API key,
council YAML, or MCP host. Keep the full checkout: MCP uses the sibling `core/`
package, and the demo reads checkout-local samples.

The demo illustrates the review format and safety boundaries, **not** model
quality or independent product validation. Details and host setup:
[mcp/README.md](mcp/README.md).

### 3. Optional: run a live deliberation

**This calls OpenRouter and may spend money.** Copy and review the example
council config first: it includes paid models and a `10.0` USD per-question
budget. Configure two distinct judges outside the council and two distinct
`router_classifiers`.

```bash
cp core/zhoda.yaml.example core/zhoda.yaml
# Set OPENROUTER_API_KEY in the repo-root or core/.env; never commit it.
# Edit core/zhoda.yaml to choose models and a budget before running:
uv --directory core run zhoda deliberate "Monolith or microservices for a 4-person B2B SaaS MVP?"
```

## Architecture (implemented and planned)

| Layer | Package | What it is |
|---|---|---|
| Core | `zhoda-core` | Implemented Python library and CLI: protocol router, elicitation, factions, debate rounds, consensus, verdicts, reputation |
| MCP | `zhoda-mcp` | Implemented Model Context Protocol server with in-process core — stdio by default, optional MCP SSE transport |
| Plugin | `@zhoda/dsh-plugin` (planned) | Not implemented or published: DeepSeek Harness debate room UI, faction graph, verdict panel |

The hosted FastAPI core server is **not implemented**. MCP's optional SSE
transport does not provide a remote core API; `ZHODA_CORE_URL` is reserved and
not wired. DeepSeek Harness can use the existing MCP integration without the
planned UI plugin.

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
