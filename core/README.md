# zhoda-core

The deliberation engine of Zhoda: a Python 3.12+ library and CLI.

Takes a question, interviews the user (Stage 0 elicitation), forms model factions,
runs Oxford-style debate rounds, detects consensus (*zhoda*), and returns a verdict
with a minority report and a full transcript.

Design doc: [../docs/01-core.md](../docs/01-core.md) · Protocol: [../docs/whitepaper.md](../docs/whitepaper.md)

## Status

Implemented: protocol routing, elicitation, model positions and factions,
debate rounds, consensus checks, verdicts with minority reports, and transcripts.
The CLI runs the engine directly; the [MCP server](../mcp/README.md) embeds it
in-process. Tests cover the protocol with scripted providers and mocked HTTP.

The hosted FastAPI server is **not implemented**. There is no `api/` package or
remote core service to start. `zhoda-core` is **not published to PyPI yet**;
use a source checkout. See the [root README](../README.md) for project status.

## Install and test from source (no API key)

Prerequisites: Python 3.12+, `uv`, and the full repository checkout.
Run these commands from the repository root, not from `core/`:

```bash
uv --directory core sync --python 3.12 --locked
uv --directory core run zhoda --help
uv --directory core run python -c "from zhoda_core.engine import ZhodaEngine; print(ZhodaEngine.__name__)"
uv --directory core run pytest -m "not live"
```

`uv sync` installs the local package and development dependencies and may need
network access to download dependencies. The import, help command, and non-live
tests need no API key, `.env`, or council YAML and make no model API calls.
The import only loads the library; it does not start a council.

Always keep `-m "not live"` for offline core tests. The live smoke test is marked
`live`, but a bare `pytest` can still select it when an API key is available.
Some tests read fixtures under the repository's `docs/` and `core/eval/`, so
keep the full checkout rather than copying just this package directory.

### Offline review demo

To see a review artifact without an API key or a running MCP host:

```bash
uv --directory mcp sync --python 3.12 --locked
uv --directory mcp run zhoda-review-demo
```

This is the existing **scripted** ADR/plan demo, not a live deliberation or
independent validation. It reads checkout-local samples and returns review JSON
with `approved: false` and `demo.live: false`.
See [MCP onboarding](../mcp/README.md#try-the-offline-review-demo-no-api-key).

## Live CLI usage (optional; calls OpenRouter)

Review the model choices and budget before running. The example uses paid
models and a `10.0` USD per-question cap. Two distinct judges must sit outside
the council, and `router_classifiers` must name two distinct models.

From the repository root, after syncing core:

```bash
cp core/zhoda.yaml.example core/zhoda.yaml
# Edit core/zhoda.yaml for your models and budget.
# Set OPENROUTER_API_KEY in the repo-root or core/.env; never commit it.
uv --directory core run zhoda deliberate "Which database for a 10k RPS event store?"
```

The CLI loads the nearest `.env` up to the git root without overriding existing
environment variables. The default clarification mode is interactive.
`--auto-clarify` avoids prompts and records unanswered ambiguities; it is **not**
an offline mode. Use `uv --directory core run zhoda deliberate --help` for
`--config`, repeatable `--context` files, and other options.

## Source layout

Selected implemented modules (not a planned tree):

```text
core/
├── pyproject.toml
├── uv.lock
├── zhoda.yaml.example      # council, judges, router, budget
├── src/zhoda_core/
│   ├── engine.py           # ZhodaEngine orchestration
│   ├── models.py           # Pydantic value maps, positions, verdicts, accounting
│   ├── config.py           # YAML loading and engine/provider construction
│   ├── cli.py              # zhoda deliberate
│   ├── router.py
│   ├── elicitor.py
│   ├── positions.py
│   ├── factions.py
│   ├── debate.py
│   ├── consensus.py
│   ├── verdict.py
│   ├── evidence.py
│   ├── transcripts.py
│   ├── replay.py
│   ├── providers/          # OpenRouter: BYOK, retries, budget, sqlite cache
│   ├── reputation/
│   ├── benchmarks/
│   └── eval/
├── eval/                  # local evaluation fixtures and datasets
└── tests/                 # scripted engine and mocked provider tests
```

## Development checks

From the repository root, after `uv sync` above. These mirror the current
[core CI checks](../.github/workflows/test.yml), including its scoped mypy target:

```bash
uv --directory core run ruff check .
uv --directory core run mypy \
  src/zhoda_core/benchmarks src/zhoda_core/actions.py \
  src/zhoda_core/stage_dtos.py src/zhoda_core/models.py \
  src/zhoda_core/evidence.py src/zhoda_core/claims.py \
  src/zhoda_core/replay.py src/zhoda_core/providers/openrouter.py
uv --directory core run pytest -m "not live"
```
