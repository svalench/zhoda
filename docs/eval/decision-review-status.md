# Decision-review statuses

Do not collapse these three into one “ready” label.

| Layer | Status | Meaning |
|---|---|---|
| Implementation | **IMPLEMENTATION_READY** | Offline MCP review, tests, scripted demo without API keys. |
| Pilot prep | **READY_FOR_APPROVAL** | Invite + feedback form drafted. **Not sent.** Permissions not obtained. |
| Pilot execution | **not started** | No volunteer live calls. Not `PILOT_COMPLETE`. |
| Product gate | **OPEN** | No confirmed useful-correct fixes + reuse study. Offline demo ≠ user effect. |
| Gold labels | **AWAITING_OWNER** | A vs B logged. `expected_action` exact 30/36 = 0.833. `abstain_policy` 36/36 = 1.000, Cohen's κ = 1.000 (P5 kappa-below-0.6 gate **not** triggered). `unacceptable_claims` exact 0/36. All 36 ids `disputed` in `gold-merged-draft.jsonl`. `gold.jsonl` unchanged. |

G protocol usefulness: **unknown**
([g-protocol-policy.md](g-protocol-policy.md)). Default review protocol
remains `debate`.

2026-09-06: S4 `docs/live-runs/2026-09-06-g-pilot-p5/report.json`
verdict=`short_review_wins_primary` (not preregistered rule 2: `|Δ|=0.125>0.10`).
Δ=0.125, n=32, spend=$0.381 / $2.00. Classes n≥5: adr_plan_review 0.00,
correct_minority +0.17, counterfactual +0.40, evidence_required 0.00,
premise_pair 0.00; legitimate_uncertainty n=4 Δ=+0.25. Rule 3 Oxford class
opt-in: none. Product default stays `debate`. Not validated
(gold sidecar heuristic; `independent_validation=false`).

Telemetry: user source documents are **not** collected by default.

Stars are not a quality metric for this workflow.
