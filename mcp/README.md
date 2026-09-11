# zhoda-mcp

MCP server for Zhoda. Thin wrapper: all deliberation lives in `zhoda-core`.
Works in DeepSeek Harness, Claude Code, Codex, and any MCP host.

Design doc: [../docs/02-mcp-server.md](../docs/02-mcp-server.md)

## Status

In-process core over **stdio** (default). SSE via `ZHODA_MCP_TRANSPORT=sse`.
`ZHODA_CORE_URL` is reserved — unset it; remote core is not wired yet.

## Tools

| Tool | What it returns |
|---|---|
| `zhoda_clarify` | Stage 0 questions + cost/time `estimate` |
| `zhoda_deliberate` | `confirm=false` → estimate; `confirm=true` → `Verdict` JSON |
| `zhoda_verdict` | Stored verdict by `transcript_id` |
| `zhoda_transcript` | хроніка as `json` or `md` |
| `zhoda_review` | Read-only ADR/RFC/plan review (`zhoda.review.v1`). `confirm=false` estimates. Never writes, never fetches URLs. |
| `zhoda_reputation` | Per-domain model ratings |

`zhoda_deliberate` never starts the council until the host passes `confirm=true`.
`zhoda_review` is the same confirm gate. It does **not** apply patches, open
issues, or merge. `approved` is always false; incomplete/degraded runs are not
recommended. Default `protocol_policy` is `debate` because the short_review
eval is not a live usefulness result.
On OpenRouter quota exhaustion `zhoda_review` returns `status=incomplete` with
`error=quota_exceeded` and `approved=false` — it does not silently degrade.
`zhoda_deliberate` still returns the structured `{error: quota_exceeded}` object.
`budget_usd` cannot raise a yaml cap of `$0` (`:free` only).

## Install (source checkout)

Prerequisites: Python 3.12+, `uv`, and the full repository checkout.
`zhoda-mcp` and `zhoda-core` are **not published to PyPI yet**. MCP's uv project
installs the sibling `../core` package as an editable dependency; do not copy
`mcp/` out of the repository.

Run all shell commands below from the repository root unless noted otherwise:

```bash
uv --directory mcp sync --python 3.12 --locked
```

The initial sync may download dependencies. It does not need an OpenRouter key.

### Try the offline review demo (no API key)

```bash
uv --directory mcp run zhoda-review-demo
# Equivalent module entry point:
uv --directory mcp run python -m zhoda_mcp.demo_review
```

The demo reads the sample ADR and plan in [examples/review/](examples/review/)
and uses a **scripted engine**; it makes no model API calls and needs no `.env`,
council YAML, or MCP host. It creates temporary local runtime storage but does
not modify the sample documents. The samples are read from the source checkout,
not packaged as wheel data.

It prints a `zhoda.review.v1` JSON report containing:

- `status: "review"` and `approved: false`;
- findings about the unique constraint, customer emails, and a feature flag;
- `demo.live: false`, `demo.independent_validation: false`, and
  `demo.product_gate: "OPEN"`;
- `cost.usd_status: "unknown"` — not a measured live model cost.

This demonstrates the review format and read-only boundaries, **not** model
quality, a live user study, or independent product validation.

### Start the server for live use

After syncing, copy and edit the council config. The example includes paid
models and a `10.0` USD per-question budget:

```bash
cp core/zhoda.yaml.example core/zhoda.yaml
# Edit core/zhoda.yaml for your models and budget.
# Set OPENROUTER_API_KEY in the repo-root .env; never commit it.
ZHODA_COUNCIL="$PWD/core/zhoda.yaml" uv --directory mcp run zhoda-mcp
```

The server uses stdio by default and waits for an MCP host.
`zhoda_deliberate` and `zhoda_review` with `confirm=false` estimate without a
model call. `zhoda_clarify` and confirmed deliberation/review use OpenRouter
and may spend money; they are not the offline demo.

## Connect a host

Prerequisite: the source install and council configuration above.
Council YAML at `core/zhoda.yaml` (copied from `core/zhoda.yaml.example`;
judges **outside** the council).
`OPENROUTER_API_KEY` in the repo-root `.env` — never in git.

Protocol: call `zhoda_deliberate` with `confirm=false` first (estimate).
Run the council only after `confirm=true`. Optional: `zhoda_clarify`, then
pass `value_map` into deliberate.

A debate is 15–40 model calls and often several minutes. Raise host
timeouts; the default 60s MCP tool timeout will kill it.

### Cursor

Project file is already [`.cursor/mcp.json`](../.cursor/mcp.json)
([example](examples/cursor.mcp.json)). Global alternative:
`~/.cursor/mcp.json`. Project wins on the same server name.

```json
{
  "mcpServers": {
    "zhoda": {
      "type": "stdio",
      "command": "uv",
      "args": ["--directory", "${workspaceFolder}/mcp", "run", "zhoda-mcp"],
      "envFile": "${workspaceFolder}/.env",
      "env": {
        "ZHODA_COUNCIL": "${workspaceFolder}/core/zhoda.yaml",
        "ZHODA_BUDGET_USD": "10"
      }
    }
  }
}
```

If the Cursor GUI cannot find `uv`, set `command` to the absolute path
(`which uv`, often `/opt/homebrew/bin/uv`). Reload: **Customize → MCP**,
toggle Zhoda, or restart Cursor. Logs: Output panel → **MCP Logs**.

Tools keep their names: `zhoda_clarify`, `zhoda_deliberate`, …

Docs: [Cursor MCP](https://cursor.com/docs/mcp).

### DeepSeek Harness

Native DSH is **not** `mcpServers` JSON. One plugin instance = one MCP
server, mounted in the user patch
(`$DSH_HOME/profiles/web/cordis.patch.yml`, default `~/.dsh/...`).
If the file is `[]`, replace it. Do not paste the API key — export it in
the shell that starts `dsh web`.

Copy [examples/dsh.cordis.patch.yml](examples/dsh.cordis.patch.yml) and
replace `/ABS/PATH/zhoda` with the clone path:

```yaml
- insert:
    - id: mcp-zhoda
      name: "@deepseek-ai/dsh-mcp-client"
      config:
        serverName: zhoda
        transport: stdio
        command: uv
        args:
          - --directory
          - /ABS/PATH/zhoda/mcp
          - run
          - zhoda-mcp
        env:
          OPENROUTER_API_KEY: !!js process.env.OPENROUTER_API_KEY
          ZHODA_COUNCIL: /ABS/PATH/zhoda/core/zhoda.yaml
          ZHODA_BUDGET_USD: "10"
        toolCallTimeoutMs: 600000
        failOnStartupError: true
```

Tools appear as `mcp__zhoda__zhoda_clarify`, `mcp__zhoda__zhoda_deliberate`, …
Check: `dsh web --dump-config | grep -A4 mcp-zhoda`.

[examples/dsh.json](examples/dsh.json) is only for generic `mcpServers`
hosts (and community managers that read that shape). Claude Code / Codex:
[examples/claude-code.json](examples/claude-code.json),
[examples/codex.json](examples/codex.json).

Env:

| Variable | Role |
|---|---|
| `OPENROUTER_API_KEY` | BYOK, required to run (not for a confirm=false estimate) |
| `ZHODA_COUNCIL` | Path to `zhoda.yaml` |
| `ZHODA_BUDGET_USD` | Overrides `budget_per_question_usd` |
| `ZHODA_TRANSCRIPTS_DIR` | хроніка directory |
| `ZHODA_REPUTATION_PATH` | Domain ELO JSON |
| `ZHODA_MCP_TRANSPORT` | `stdio` (default) or `sse` |
| `ZHODA_CORE_URL` | Reserved; setting it returns `remote_core_unwired` |

## Read-only decision review

`zhoda_review` accepts `source_text` and/or `source_paths` under explicit
`allowed_roots`. It does not scan the home directory or the git root.
Local files only; no URL fetch. Secrets are redacted before the council.
Transcripts stay in `ZHODA_TRANSCRIPTS_DIR`. User documents are not sent
as telemetry, but confirmed live reviews send the assembled source context
to the OpenRouter council models.

Trust: a `plan_proposal` is for the human, not for an executor agent to
apply. Cancel and timeout return `status=incomplete` with a transcript
error event — not a successful verdict.

Statuses: [decision-review-status.md](../docs/eval/decision-review-status.md).
Implementation is **IMPLEMENTATION_READY**. The volunteer pilot is **not**
complete. Product gate is **OPEN**. The offline demo is not user impact.

Start with the [offline review demo](#try-the-offline-review-demo-no-api-key)
before configuring a live host.

## Development checks

From the repository root, after syncing MCP. The current
[MCP CI job](../.github/workflows/test.yml) runs these checks; its tests use
fake engines and mocked/scripted providers, not live model calls:

```bash
uv --directory mcp run ruff check .
uv --directory mcp run mypy src
uv --directory mcp run pytest
```
