# Annotator instructions (pilot holdout)

## Status of current labels

All gold rows in `core/eval/pilot/gold.jsonl` are **provisional**.
They were drafted by someone who (1) implemented protocol/eval code and
(2) had seen 2026-09-05 XOR-10 / tiny-replay outputs.

That is **not** independent expert annotation. Do not cite these labels
as a human expert panel.

Until a second annotator finishes the set:

- `label_status` stays `provisional`
- `annotator` stays `protocol-author-not-independent`
- disagreement log remains **not started** (template only)

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

Synthetic sources only. If a future source has personal data, **do not**
send it to an external provider without written permission.

## Disagreement

Record conflicts in `docs/eval/disagreement-log.md`. Resolve without
knowing arm identity or the eventual winner. After resolution, bump
`label_status` to `adjudicated` in a **new** gold file version. Do not
silently rewrite the provisional file in place if a live run already
used it.
