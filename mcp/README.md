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
| `zhoda_reputation` | Per-domain model ratings |

`zhoda_deliberate` never starts the council until the host passes `confirm=true`.
On OpenRouter quota exhaustion the tool returns `{"error": "quota_exceeded", ...}`
— it does not silently degrade.

## Install (from this repo)

```bash
cd mcp
uv sync
# OPENROUTER_API_KEY in repo-root .env or here
# ZHODA_COUNCIL=/path/to/zhoda.yaml  (default: ./zhoda.yaml)
uv run zhoda-mcp
```

## Connect a host

Prerequisite: `cd mcp && uv sync`. Council YAML at `core/zhoda.yaml`
(copy from `zhoda.yaml.example`; judges **outside** the council).
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

## Tests

```bash
cd mcp
uv sync
uv run pytest
```
