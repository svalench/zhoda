# zhoda-mcp

MCP server for Zhoda — brings the council into any MCP-compatible agent harness
(DeepSeek Harness, Claude Code, Codex).

Design doc: [../docs/02-mcp-server.md](../docs/02-mcp-server.md)

## Status

🚧 Planned — after core MVP.

## Tools

| Tool | Purpose |
|---|---|
| `zhoda_clarify` | Stage 0 only: clarifying questions for a raw question |
| `zhoda_deliberate` | Full deliberation cycle → structured Verdict |
| `zhoda_verdict` | Re-read a verdict by transcript id |
| `zhoda_transcript` | Full debate transcript (md/json) |
| `zhoda_reputation` | Per-domain model ratings |

## Install (planned)

```bash
uvx zhoda-mcp
```

```jsonc
{
  "mcpServers": {
    "zhoda": {
      "command": "uvx",
      "args": ["zhoda-mcp"],
      "env": { "OPENROUTER_API_KEY": "sk-or-...", "ZHODA_BUDGET_USD": "0" }
    }
  }
}
```
