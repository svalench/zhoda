# G — protocol policy for decision review

Status: **documented, not empirically closed.**

Eval holdout for Oxford vs `short_review` is
[2026-09-06-preregistration.md](2026-09-06-preregistration.md).
S4 P5 (`docs/live-runs/2026-09-06-g-pilot-p5/report.json`): paired Δ=0.125,
n=32, spend=$0.381 / $2.00, verdict=`short_review_wins_primary`. That is not
preregistered rule 2 (`|Δ|≤0.10` and cheaper). Usefulness of `short_review` as
a product default remains **unknown**. Not validated
(gold sidecar heuristic; `independent_validation=false`).

## Decision for this workflow

| Item | Choice |
|---|---|
| Product default | still `debate` (router). Unchanged. |
| `zhoda_review` default `protocol_policy` | `debate` |
| `short_review` | opt-in only (`protocol_policy=short_review`) |
| Claim that short_review should replace Oxford | **not made** |

A Critical on the eval, or a live study that is not validated, blocks treating
`short_review` as the review default. Hosts may still pass it explicitly.

This is not product-gate evidence.
