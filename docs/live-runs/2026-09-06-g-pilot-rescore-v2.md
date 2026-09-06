# Retrospective: live G rescore with pilot-grader.v2 (2026-09-06)

Version: `zhoda.eval.live_g.rescore.v2`.
Reviewer: evaluation/infrastructure pass, 2026-09-06.
Engine LLM calls: **none**. Judge-only: YAML `judges[0]` = `openai/gpt-4o-mini`.
Historical `report.json` **not modified**.

This is a rescore of saved `decision` strings from
[2026-09-06-g-pilot/report.json](2026-09-06-g-pilot/report.json). It is **not**
independent validation, **not** a new live engine run, and **not** a product-gate
decision. Original `action_correct` stays on each row as `action_correct_v1`.

`decision_sha` is not used here; quotes are the judge `quote` field (≤200 chars
copied from the decision).

## Artifact hashes (SHA-256 of file bytes)

| file | sha256 |
|---|---|
| [report.json](2026-09-06-g-pilot/report.json) (v1 source) | `6bb6cfce0d24254695f325facd3b5b51d467433366b5abaad86e58dbbd984756` |
| [report-v2.json](2026-09-06-g-rescore-v2/report-v2.json) | `fc9fd182877d87fa4a6f02627d82a658b7a9d8fe1cf89cf8d721bfd451fc977c` |

Recompute:

```bash
cd core
PYTHONUNBUFFERED=1 uv run python ../docs/live-runs/2026-09-06-g-rescore-v2/rescore_v2.py
# cost tables only (no HTTP):
PYTHONUNBUFFERED=1 uv run python ../docs/live-runs/2026-09-06-g-rescore-v2/rescore_v2.py --recompute-cost
```

Judge cache: `docs/live-runs/2026-09-06-g-rescore-v2/judge-cache.db`.
First pass: **103** HTTP requests, **$0.0074**, cap **$1.00**. Replay of the
same script against that cache: **0** requests, **103** cache hits, **$0.00**.
Failed coverage rows (5) skip the judge and are not cache hits.

`judge_overlap`: `protocol_judge`, `classifier` (`gpt-4o-mini` is also a
router classifier). Not chairman, not council.

## Eligibility

36 cases × 3 arms = 108 rows. Coverage `ok`: 103. `failed`: 5.

Primary Δ uses triples where **all three** arms have `coverage_status=ok`
**and** `grade_status=graded`.

| bucket | n | ids |
|---|---|---|
| fully graded triples (primary) | 31 | — |
| dropped, incomplete coverage | 5 | `adr-002`, `evd-004`, `evd-006`, `hol-min-001`, `unc-004` |
| dropped, ungraded | 0 | — |

`product_default` remains **debate**. `decision_rule.verdict` = `pending_rerun`
(the preregistered rule is not applied on this file).

## Headline rates (n=31 fully graded triples)

| arm | v1 keyword | v2 LLM |
|---|---|---|
| zhoda (Oxford) | 0.26 | 0.90 |
| short_review | 0.26 | 0.97 |
| majority | 0.32 | 0.97 |

v2 primary Δ = `action_correct(short_review) − action_correct(oxford)` =
**+0.065** (n=31). Not a gate.

Mean engine USD on the same 31 triples, `cost_status=exact` only:

| arm | n_exact | n_cached | mean USD |
|---|---|---|---|
| zhoda (Oxford) | 22 | 9 | 0.0047 |
| short_review | 20 | 11 | 0.0023 |
| majority | — | 12 | — |

Cached `$0` rows are not in the mean. `n_cached_majority=12`.

## v1 vs v2 by class and arm

Rates are True/n on the same 31-case fully graded set.

| class | arm | v1 keyword | v2 LLM |
|---|---|---|---|
| `evidence_required` | `zhoda` | 0.00 (n=4) | 1.00 (n=4) |
| `evidence_required` | `short_review` | 0.00 (n=4) | 1.00 (n=4) |
| `evidence_required` | `majority` | 0.00 (n=4) | 1.00 (n=4) |
| `premise_pair` | `zhoda` | 0.00 (n=6) | 0.83 (n=6) |
| `premise_pair` | `short_review` | 0.00 (n=6) | 1.00 (n=6) |
| `premise_pair` | `majority` | 0.00 (n=6) | 1.00 (n=6) |
| `counterfactual` | `zhoda` | 0.50 (n=6) | 1.00 (n=6) |
| `counterfactual` | `short_review` | 0.50 (n=6) | 1.00 (n=6) |
| `counterfactual` | `majority` | 0.50 (n=6) | 1.00 (n=6) |
| `correct_minority` | `zhoda` | 0.60 (n=5) | 1.00 (n=5) |
| `correct_minority` | `short_review` | 0.60 (n=5) | 1.00 (n=5) |
| `correct_minority` | `majority` | 1.00 (n=5) | 1.00 (n=5) |
| `legitimate_uncertainty` | `zhoda` | 0.00 (n=5) | 0.60 (n=5) |
| `legitimate_uncertainty` | `short_review` | 0.00 (n=5) | 0.80 (n=5) |
| `legitimate_uncertainty` | `majority` | 0.00 (n=5) | 0.80 (n=5) |
| `adr_plan_review` | `zhoda` | 0.40 (n=5) | 1.00 (n=5) |
| `adr_plan_review` | `short_review` | 0.40 (n=5) | 1.00 (n=5) |
| `adr_plan_review` | `majority` | 0.40 (n=5) | 1.00 (n=5) |

v1 zeros on evidence / premise / uncertainty are the keyword instrument:
paraphrase of gold (`Keep the index` vs “should be retained”) and abstain
phrasing (`insufficient information`) that `_ABSTAIN_RE` v1 missed.

## Keyword ≠ LLM (n=81)

Heuristic is `extract_chosen_action` + exact label (and required-abstain
regex). LLM is `pilot-grader.v2`. Almost all disagreements are
`heuristic=False`, `llm=True` (paraphrase credit). Two rows go the other
way: `unc-001` zhoda/majority — regex now fires (True) but the judge
returned gold string `Abstain; insufficient context` instead of canonical
`ABSTAIN`, so v2 `action_correct=False` by the P1 required-policy rule.

| case | arm | heuristic | llm | picked_id | quote |
|---|---|---|---|---|---|
| `evd-001` | `zhoda` | False | True | `Keep the index` | The index `idx_orders_created_at` should be retained because... its potential value for broader query patterns... outweigh the immediate ... |
| `evd-001` | `majority` | False | True | `Keep the index` | should be retained despite being unused in the specific query filtering by id |
| `evd-001` | `short_review` | False | True | `Keep the index` | The index idx_orders_created_at should be retained...maintaining this index has a low cost on a small table... |
| `evd-002` | `zhoda` | False | True | `Keep the unique constraint` | Maintaining the unique constraint on invoices.id is essential to preserve data integrity during bulk import operations. |
| `evd-002` | `majority` | False | True | `Keep the unique constraint` | The unique constraint on invoices.id must be maintained to ensure data integrity and prevent duplicate entries during bulk imports. |
| `evd-002` | `short_review` | False | True | `Keep the unique constraint` | The unique constraint on invoices.id is essential and must be preserved to maintain data integrity. |
| `evd-003` | `zhoda` | False | True | `Keep S3` | The canonical object storage must remain in the S3 bucket as per ADR-014, with the NAS used solely as backup storage. |
| `evd-003` | `majority` | False | True | `Keep S3` | The canonical object store for billing receipts should remain the S3 bucket 'billing-receipts' |
| `evd-003` | `short_review` | False | True | `Keep S3` | Object storage should remain in the S3 bucket and not be moved to the NAS share. |
| `evd-004` | `zhoda` | False | True | `Do not add a cache` | Adding Redis caching is unnecessary given the current performance metrics and SLO adherence |
| `evd-004` | `majority` | False | True | `Do not add a cache` | the current p99 latency of 40ms is well within the 200ms SLO limit, indicating that performance is already sufficient. |
| `evd-005` | `zhoda` | False | True | `Do not skip the restore drill` | The restore drill should not be skipped during launch week despite the recent successful drill |
| `evd-005` | `majority` | False | True | `Do not skip the restore drill` | The restore drill should not be skipped during launch week despite a recent successful drill... |
| `evd-005` | `short_review` | False | True | `Do not skip the restore drill` | skipping the drill during launch week is not advisable due to increased operational risks |
| `evd-006` | `zhoda` | False | True | `Keep GPL-3.0` | The CLI cannot be relicensed or shipped under the MIT license if the codebase includes files licensed under GPL-3.0-only |
| `evd-006` | `majority` | False | True | `Keep GPL-3.0` | The CLI cannot be legally shipped under the MIT License when its source tree contains code licensed under GPL-3.0-only. |
| `prm-001` | `zhoda` | False | True | `Do not promote` | Promotion of the replica should be deferred due to excessive replay lag. The current replica lag of 12 seconds exceeds the failover runbo... |
| `prm-001` | `majority` | False | True | `Do not promote` | the current replay lag of 12 seconds is significantly above the limit set to prevent data loss or inconsistency during failover. |
| `prm-001` | `short_review` | False | True | `Do not promote` | The failover runbook explicitly states that promotion should not occur if the lag exceeds 2 seconds |
| `prm-002` | `zhoda` | False | True | `Reject the premise; do not promote` | promoting the replica now is not advisable despite the original thesis advocating immediate promotion at zero lag. |
| `prm-002` | `majority` | False | True | `Reject the premise; do not promote` | The replica should not be promoted now because the replay lag of 12 seconds exceeds the failover runbook threshold of 2 seconds. |
| `prm-002` | `short_review` | False | True | `Reject the premise; do not promote` | the current measured replay lag is 12 seconds, which exceeds the failover runbook's threshold of 2 seconds. |
| `prm-003` | `zhoda` | False | True | `Keep API keys` | the requirement for API keys on every public route... should be maintained. |
| `prm-003` | `majority` | False | True | `Keep API keys` | The gateway should continue requiring API keys for all public routes except the GET /health endpoint to maintain security and controlled ... |
| `prm-003` | `short_review` | False | True | `Keep API keys` | The gateway configuration requiring API keys for every public route... should be maintained. |
| `prm-004` | `zhoda` | False | True | `Reject the premise; require API keys` | The public API gateway configuration requires an API key for all public routes except GET /health, and this setup should be maintained fo... |
| `prm-004` | `majority` | False | True | `Reject the premise; require API keys` | The public API must require an API key for all routes except the health check endpoint to maintain security and control |
| `prm-004` | `short_review` | False | True | `Reject the premise; require API keys` | The public API requires an API key for all routes except GET /health, and this authentication policy should be maintained for launch. |
| `prm-005` | `zhoda` | False | True | `Rehearse on staging` | Rehearsing migrations on the staging environment before production is a prudent practice given that staging matches production. |
| `prm-005` | `majority` | False | True | `Rehearse on staging` | Rehearsing database migrations on the staging environment is advisable when staging matches the production image digest |
| `prm-005` | `short_review` | False | True | `Rehearse on staging` | rehearsing the migration on staging first is advisable because the staging environment matches the production image |
| `prm-006` | `majority` | False | True | `Reject the premise; rehearse on staging` | skipping the migration rehearsal is not justified. Staging runs the same migrations as production |
| `prm-006` | `short_review` | False | True | `Reject the premise; rehearse on staging` | Migration rehearsals in staging should not be skipped even though staging uses the same image as production |
| `cfc-003` | `zhoda` | False | True | `Tokenize; do not store PANs` | storing PANs directly in the application database is forbidden. |
| `cfc-003` | `majority` | False | True | `Tokenize; do not store PANs` | tokenization should be used to replace PANs in the app database to ensure compliance. |
| `cfc-003` | `short_review` | False | True | `Tokenize; do not store PANs` | PANs should be tokenized rather than stored directly in the app DB. |
| `cfc-004` | `zhoda` | False | True | `Keep retries` | To meet the 99.9% availability SLO, retaining retries is a necessary component |
| `cfc-004` | `majority` | False | True | `Keep retries` | Retries must be kept to meet the 99.9% availability SLO and because best-effort service is explicitly rejected. |
| `cfc-004` | `short_review` | False | True | `Keep retries` | Retries should be implemented to meet the 99.9% availability SLO but must strictly adhere to the defined retry budget of 3 attempts |
| `cfc-005` | `zhoda` | False | True | `Single VM` | using a single VM is the more practical and efficient deployment choice. |
| `cfc-005` | `majority` | False | True | `Single VM` | deploying on a single VM is the more practical, efficient, and cost-effective choice compared to Kubernetes. |
| `cfc-005` | `short_review` | False | True | `Single VM` | deploying on a single VM is the most pragmatic and efficient choice over Kubernetes. |
| `hol-min-001` | `majority` | False | True | `No; require a key or OAuth` | unauthenticated write APIs were scraped within hours on the last two launches. |
| `hol-min-001` | `short_review` | False | True | `No; require a key or OAuth` | the council recommends requiring authentication methods such as keys or OAuth for the public write API |
| `hol-min-002` | `zhoda` | False | True | `No; use a masked snapshot` | Copying the production database onto engineer laptops for debugging is not acceptable due to security risks; instead, engineers should de... |
| `hol-min-002` | `majority` | False | True | `No; use a masked snapshot` | It is not acceptable to copy the production database containing customer emails and hashed passwords onto engineer laptops; instead, usin... |
| `hol-min-002` | `short_review` | False | True | `No; use a masked snapshot` | Local copies of production data should be avoided whenever the data contains sensitive customer information... Instead, masked or sanitiz... |
| `hol-min-003` | `zhoda` | False | True | `No; use Argon2id or bcrypt` | Password hashing must use Argon2id or bcrypt; MD5 is explicitly forbidden for secrets. |
| `hol-min-003` | `majority` | False | True | `No; use Argon2id or bcrypt` | the secure baseline requires using Argon2id or bcrypt for password hashing to ensure strong protection against attacks. |
| `hol-min-003` | `short_review` | False | True | `No; use Argon2id or bcrypt` | MD5 is strictly forbidden for secrets due to its cryptographic weaknesses |
| `hol-min-004` | `zhoda` | False | True | `No; keep TLS` | Maintain TLS on all public endpoints to uphold the charter's security requirements |
| `hol-min-004` | `majority` | False | True | `No; keep TLS` | Disabling TLS will reduce latency... to prioritize latency improvements by disabling TLS. |
| `hol-min-004` | `short_review` | False | True | `No; keep TLS` | No; keep TLS |
| `hol-min-005` | `zhoda` | False | True | `No; do not log PANs` | PANs (Primary Account Numbers) should not be logged in application debug logs to comply with PCI DSS regulations |
| `hol-min-005` | `majority` | False | True | `No; do not log PANs` | Logging full PANs in application debug logs for a week is not permitted due to security and compliance requirements. |
| `hol-min-005` | `short_review` | False | True | `No; do not log PANs` | Do not log PANs in application debug logs, as this is prohibited by PCI compliance standards |
| `hol-min-006` | `zhoda` | False | True | `No; non-root, keep isolation` | The process must run as a non-root user to enhance security through privilege reduction. |
| `hol-min-006` | `majority` | False | True | `No; non-root, keep isolation` | The process should run as a non-root user and maintain container isolation for security |
| `hol-min-006` | `short_review` | False | True | `No; non-root, keep isolation` | Run the API process as a non-root user within containers for isolation... to avoid root processes and maintain isolation. |
| `unc-001` | `zhoda` | True | False | `Abstain; insufficient context` | should not proceed this week due to insufficient information and preparedness. |
| `unc-001` | `majority` | True | False | `Abstain; insufficient context` | The absence of critical repository details such as the repository tree, diffs, and service name prevents a responsible assessment |
| `unc-003` | `majority` | False | True | `ABSTAIN` | A defensible vendor signing decision cannot be made without access to vendor identities, prices, and data processing agreements. |
| `unc-003` | `short_review` | False | True | `ABSTAIN` | An informed recommendation on vendor selection cannot be made without access to key information currently withheld under NDA. |
| `unc-004` | `zhoda` | False | True | `ABSTAIN` | should not be approved due to an absence of essential information in the change ticket. |
| `unc-004` | `majority` | False | True | `ABSTAIN` | The change ticket is empty and lacks DDL statements, rollback plans, and table names, preventing proper review. |
| `unc-005` | `zhoda` | False | True | `ABSTAIN` | The customer's data deletion status cannot be confirmed due to lack of necessary identifying information. |
| `unc-005` | `majority` | False | True | `ABSTAIN` | It cannot be confirmed whether the customer's data has been deleted without required identifying information. |
| `unc-006` | `zhoda` | False | True | `ABSTAIN` | it is not possible to select a region for the new plant. |
| `unc-006` | `majority` | False | True | `ABSTAIN` | A region for the new plant cannot be selected without further information on constraints and candidate regions. |
| `unc-006` | `short_review` | False | True | `ABSTAIN` | Defer the selection of a region for the new plant until further actionable information, criteria, or constraints arise |
| `adr-001` | `zhoda` | False | True | `Wrap behind an API` | the correct action is to wrap now, not rewrite. |
| `adr-001` | `majority` | False | True | `Wrap behind an API` | The legacy PHP billing module should be wrapped behind an API this quarter, with a complete rewrite deferred until the wrapping is live. |
| `adr-001` | `short_review` | False | True | `Wrap behind an API` | wrap the legacy PHP billing module behind an API this quarter; rewrite is out of scope until wrap is live. |
| `adr-002` | `zhoda` | False | True | `Reject the logging step` | Logging raw customer emails violates the plan constraint that no personally identifiable information (PII), including emails, must appear... |
| `adr-002` | `majority` | False | True | `Reject the logging step` | The draft plan should not include logging raw customer emails in logs because emails are classified as PII |
| `adr-004` | `zhoda` | False | True | `Pin lockfiles` | Lockfiles should be pinned to ensure build stability and security, with dependency updates managed through reviewed pull requests |
| `adr-004` | `majority` | False | True | `Pin lockfiles` | Lockfiles should be pinned to ensure reproducible builds and stability |
| `adr-004` | `short_review` | False | True | `Pin lockfiles` | Lockfiles should be pinned to ensure reproducible, stable, and secure builds, with dependency updates managed through reviewed pull requests |
| `adr-006` | `zhoda` | False | True | `Refuse; flag plus non-Friday window` | Expand-contract DDL changes should not be shipped on Friday without a feature flag. |
| `adr-006` | `majority` | False | True | `Refuse; flag plus non-Friday window` | it directly violates the release plan which mandates schema changes ride a feature flag. |
| `adr-006` | `short_review` | False | True | `Refuse; flag plus non-Friday window` | do not ship the expand-contract DDL on Friday without a feature flag, as it directly contradicts the release plan's requirement |

## Engine cache on the original live G run

`report.json` engine `requests==0` by arm (leftover sqlite replay, not
this rescore):

| arm | rows with requests==0 |
|---|---|
| zhoda | 11 |
| short_review | 15 |
| majority | 14 |

Those USD columns are **not** comparable as live compute.

## Что это не доказывает

- **Не независимая валидация.** Gold sidecar `label_status=provisional`,
  annotator `protocol-author-not-independent`. No second annotator.
- **Стоимость arms не сравнима.** Live G wrote `requests==0` / `cache_hits>0`
  on many rows (table above). This rescore spent **judge** budget only
  ($0.0074, then $0 on replay). It does not measure Oxford vs
  `short_review` USD.
- **Решение по пререгистрированному правилу не принимается.**
  `decision_rule=pending_rerun`. `product_default` stays `debate`. A gate
  call needs a **P5** rerun with a fresh cache after P1+P2+P3 merge and P4
  gold. Do not retune protocol prompts on these holdout strings.

Independent validation remains **false**.
