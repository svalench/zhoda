# Live G — Oxford vs short_review (pilot holdout)

Started: 2026-09-06T15:31:51+00:00
Stopped: 2026-09-06T16:51:25+00:00
Stop reason: `complete`
Execution SHA: `7385d911d933bc41b9833c0b2e5ab6a2212e45ac`
Frozen source_sha (identity): `bfd7e90b01515ddb09c8d00e60f8f3cf2b300277`
Spend: $0.3810 / $2.00
n complete (3 arms): 32 / 36
Ungraded/failed share: 0.046
Primary Δ = action_correct(short_review) − action_correct(oxford): **0.1250**
Decision rule: **short_review_wins_primary** — Δ=0.125 > 0.10; short_review лучше по primary. Default в коде не меняем без отдельного решения owner.
Product default in code: **debate** (not flipped by this script).

Independent validation: **false**. Gold is provisional / protocol-author-not-independent.
No p-value. Blind LLM judge was not spent during the engine loop (gold sidecar after attempts).
Do not retune prompts on these holdout outputs.
