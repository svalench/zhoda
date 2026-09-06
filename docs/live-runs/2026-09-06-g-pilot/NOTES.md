# Live G — Oxford vs short_review (pilot holdout)

Started: 2026-09-06T09:24:05+00:00
Stopped: 2026-09-06T09:59:36+00:00
Stop reason: `complete`
Execution SHA: `2f8694c1d988e37083a56b476db6ae6596a629cc`
Frozen source_sha (identity): `bfd7e90b01515ddb09c8d00e60f8f3cf2b300277`
Spend: $0.2116 / $8.00
n complete (3 arms): 31 / 36
Ungraded/failed share: 0.046
Primary Δ = action_correct(short_review) − action_correct(oxford): **0.0000**
Decision rule: **recommend_short_review_default** — rule2: |Δ|=0.000≤0.10 and mean USD short_review 0.0015 < oxford 0.0033; code default unchanged until owner
Product default in code: **debate** (not flipped by this script).

Independent validation: **false**. Gold is provisional / protocol-author-not-independent.
No p-value. Blind LLM judge was not spent during the engine loop (gold sidecar after attempts).
Do not retune prompts on these holdout outputs.
