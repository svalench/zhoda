# Annotator instructions (pilot holdout)

## Status of current labels

All gold rows in `core/eval/pilot/gold.jsonl` are **provisional**.
They were drafted by someone who (1) implemented protocol/eval code and
(2) had seen 2026-09-05 XOR-10 / tiny-replay outputs.

That is **not** independent expert annotation. Do not cite these labels
as a human expert panel.

Second annotator file: `core/eval/pilot/gold-annotator-b.jsonl`
(`llm-independent:cursor-grok-4.6`). Owner resolution is **empty**.

Until the owner fills `docs/eval/disagreement-log.md`:

- `core/eval/pilot/gold.jsonl` stays `provisional` /
  `protocol-author-not-independent` (do not rewrite it in place)
- `gold-merged-draft.jsonl` marks exact A/B field diffs as `disputed`
- disagreement log is **OPEN**, not adjudicated

## How to label (second annotator)

Work from `public.jsonl` + the attached `source_bundle.text` only.
Do not look at arm outputs or which protocol “should win.”

For each id:

1. `expected_action` — the action you would take given the source.
2. `allowed_alternatives` — other actions that are still acceptable.
3. `gold_conditions` — what must be true of a credited answer (e.g. quote
   a span, reject a false premise, abstain).
4. `unacceptable_claims` — if the system asserts these, it is a false
   positive even if the action string looks close.
5. `abstain_policy` — `required` | `allowed` | `forbidden`.
6. `severity` is already on the public row (task metadata). Do not change
   it without a version note.

If the source is too thin to decide, set `abstain_policy=required` and
`expected_action` to an abstain form. Do not invent facts.

## Как считается `action_correct` v2

`grader_version=pilot-grader.v2` (`zhoda_core.eval.grading`).

1. **LLM-судья** (YAML `judges[0]`, не chairman и не член совета). В
   промпт попадают `question`, `source_bundle.text` и закрытый список
   labels. Имя arm и `Recommended (majority at cap…)` в промпт не
   кладутся: `dissent:` / `minority:` / `minority report:` (любой регистр)
   и карта `No zhoda (split|deadlock|…)` срезаются (`judge_visible_decision`);
   судье остаётся маркер, не тезисы Response A/B/C. `quote` должен быть
   непустой span этого head, иначе `ungraded`. `"committed": "false"`
   строкой и `picked_id` вне labels →
   `grade_status=ungraded`, не incorrect. YAML `judges[0]` не chairman и
   не член совета.
2. **Зачёт.** Если `abstain_policy=required`, `action_correct` при
   `picked_id == "ABSTAIN"` **или** золотой abstain-метке.
3. **Heuristic** — keyword-путь; парафраз без exact label остаётся False.
4. **Abstain regex** только до `dissent:` / `minority:` / маркера `No zhoda`. «insufficient information» —
   abstain; «insufficient index coverage is not the issue» — нет.

Mean USD в таблицах — только `cost_status=exact`; `n_cached` отдельно.

Synthetic sources only. If a future source has personal data, **do not**
send it to an external provider without written permission.

## Disagreement

Record conflicts in `docs/eval/disagreement-log.md`. Resolve without
knowing arm identity or the eventual winner. After resolution, bump
`label_status` to `adjudicated` in a **new** gold file version. Do not
silently rewrite the provisional file in place if a live run already
used it.
