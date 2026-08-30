# Licensing

Zhoda uses a split license (decision record: docs/04-critique-response.md §6):

| Part | License | Why |
|---|---|---|
| `core/src/zhoda_core/` engine | Apache-2.0 | maximum integrations |
| `mcp/`, `plugins/` | Apache-2.0 | distribution channels |
| `core/src/zhoda_core/api/` (hosted server, when it exists) | AGPL-3.0 | protection from hosted clones |

The moat is reputation data, brand, and community — not the code.

Canonical license texts: [LICENSE-APACHE](LICENSE-APACHE) (Apache-2.0) and
[LICENSE-AGPL](LICENSE-AGPL) (AGPL-3.0).
