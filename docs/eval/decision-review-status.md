# Decision-review statuses

Do not collapse these three into one “ready” label.

| Layer | Status | Meaning |
|---|---|---|
| Implementation | **IMPLEMENTATION_READY** | Offline MCP review, tests, scripted demo without API keys. |
| Pilot prep | **READY_FOR_APPROVAL** | Invite + feedback form drafted. **Not sent.** Permissions not obtained. |
| Pilot execution | **not started** | No volunteer live calls. Not `PILOT_COMPLETE`. |
| Product gate | **OPEN** | No confirmed useful-correct fixes + reuse study. Offline demo ≠ user effect. |

G protocol usefulness: **unknown**
([g-protocol-policy.md](g-protocol-policy.md)). Default review protocol
remains `debate`.

Telemetry: user source documents are **not** collected by default.

Stars are not a quality metric for this workflow.

`pilot-grader.v1` results from 2026-09-06 live G (`report.json`) are
**invalid as an instrument**: keyword `extract_chosen_action` scored
paraphrases and unc-* abstentions as incorrect. Retrospective v2:
[2026-09-06-g-pilot-rescore-v2.md](../live-runs/2026-09-06-g-pilot-rescore-v2.md).
Do not treat those `action_correct` columns as protocol evidence.
`decision_rule=pending_rerun`. Product default remains `debate`.
