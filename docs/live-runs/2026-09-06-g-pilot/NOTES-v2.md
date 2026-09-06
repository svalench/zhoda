# Live G — offline rescore v2 (executable grader, no engine)

Source: `report.json` (schema `zhoda.eval.live_g.v1`, SHA-256
`6bb6cfce0d24254695f325facd3b5b51d467433366b5abaad86e58dbbd984756`).
This file does **not** replace NOTES.md or report.json.

Rescored: 2026-09-06 via `python -m zhoda_core.eval rescore-report --judge none`.
Judge: executable gold sidecar only. **No OpenRouter. No engine.**
`blind_llm_judge`: false. Independent validation: **false**.

| | v1 (heuristic label match) | v2 (allowed-label / ungraded) |
|---|---|---|
| n complete (3 arms) | 31 / 36 | 31 / 36 |
| attempts | 108 | 108 |
| action_correct true / false / ungraded | 28 / 80 / 0 | 27 / 26 / 55 |
| ungraded/failed share | 0.046 | **0.556** |
| paired Δ | 0.000 | −0.032 |
| decision rule | recommend_short_review_default | **inconclusive** (rule 1: ungraded/failed share > 0.20) |
| product default in code | debate | debate |

Counterexample: `evd-001` zhoda decision paraphrases “keep the index”.
v1 `action_correct=false`. v2 `ungraded` (`paraphrase_not_a_label`).

`--judge llm` was **not** spent in this file. A later judge overlay would be
`report-v2-llm.json`, not a rewrite of this v2.

Do not treat v2 as a closed product gate. Gold remains provisional.
