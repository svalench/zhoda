# G — protocol policy for decision review

Status: **documented, not empirically closed.**

Eval holdout for Oxford vs `short_review` is
[2026-09-06-preregistration.md](2026-09-06-preregistration.md)
(`READY_FOR_APPROVAL`, live=false). Usefulness of `short_review` as a
product default is **unknown**. There is no live paired Δ.

## Decision for this workflow

| Item | Choice |
|---|---|
| Product default | still `debate` (router). Unchanged. |
| `zhoda_review` default `protocol_policy` | `debate` |
| `short_review` | opt-in only (`protocol_policy=short_review`) |
| Claim that short_review should replace Oxford | **not made** |

A Critical on the eval, or a live study that has not run, blocks treating
`short_review` as the review default. Hosts may still pass it explicitly.

This is not product-gate evidence.
