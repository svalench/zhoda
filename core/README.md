# zhoda-core

The deliberation engine of Zhoda. Python 3.12+ / FastAPI.

Takes a question, interviews the user (Stage 0 elicitation), forms model factions,
runs Oxford-style debate rounds, detects consensus (*zhoda*), and returns a verdict
with a minority report and a full transcript.

Design doc: [../docs/01-core.md](../docs/01-core.md) · Protocol: [../docs/whitepaper.md](../docs/whitepaper.md)

## Status

🚧 Design stage — scaffolding. Task list: design doc, §10.

## Planned layout

```
core/
├── pyproject.toml
├── zhoda.yaml.example      # council config
├── src/zhoda_core/
│   ├── models/             # Pydantic: ValueMap, Position, Critique, FactionSwitch, Verdict
│   ├── providers/          # OpenRouter (BYOK), retries, semantic cache
│   ├── stages/             # elicitor, positions, factions, debate, consensus, verdict
│   ├── engine/             # orchestration state machine
│   ├── api/                # FastAPI: /v1/clarify, /v1/deliberate (SSE), /v1/transcript, /v1/reputation
│   └── cli.py              # `zhoda deliberate "..."`
└── tests/                  # fixtures: consensus / split / escalation scenarios
```

## Quickstart (planned)

```bash
cd core
uv sync
# OPENROUTER_API_KEY in ../.env or ./.env
uv run zhoda deliberate "Which database for a 10k RPS event store?" --auto-clarify
```
