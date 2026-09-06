# Preregistered pilot validation — 2026-09-06

Status: **READY_FOR_APPROVAL**. Live = false. Independent validation = false.
This file is the plan for the owner. Empty result tables are not a completed study.

Protocol authors (this session) implemented engine/eval F and read 2026-09-05
XOR-10 / tiny-replay outputs. Gold labels below are **provisional**, not
independent expert annotation.

## Frozen identity (before any outputs)

| Item | Value |
|---|---|
| F acceptance | `6486e8e` `fix(eval): validate effective runs and resource-matched scoring.` |
| Short-review prereq | `bfd7e90` `feat(protocol): add opt-in short_review ablation path.` |
| Product default | still `debate` (router). `short_review` is **force only** |
| Rubric | `zhoda.eval.quality.v1` content hash via `hash_rubric()` |
| Prompts | `hash_prompts()` includes Oxford + `EVIDENCE_CRITIQUE_PROMPT` |
| Dataset split | existing 51 cases = **development**; this 36 = **pilot holdout** |
| Label status | `provisional` / annotator `protocol-author-not-independent` |

Content hashes of public JSONL, gold JSONL, and this plan are written by
`python -m zhoda_core.eval freeze-manifest` into
[frozen-manifest.json](frozen-manifest.json). Changing dataset, plan, prompt,
or rubric changes identity and must not reuse an old checkpoint.

## Question this run is allowed to answer

**Is the extra Oxford loop (multi-round critique / rebut / switch) useful
enough to stay the default versus a short evidence-focused review?**

Not: “prove Zhoda is best.” Not ELO. Not growing development-51 for an
accuracy headline.

## Primary hypothesis (one)

**Quality.** Primary metric: paired difference

`Δ = action_correct(short_review) − action_correct(oxford)`

on the 36 pilot cases (one replicate). `action_correct` is the F quality
axis, not keyword-in-dissent and not `zhoda_reached`.

Pre-registered decision rule:

1. If fewer than 30 cases complete, or ungraded/failed share > 20% →
   **inconclusive**. Do not relax the target.
2. If `|Δ| ≤ 0.10` (non-inferiority margin) **and** mean recorded USD of
   short_review < oxford → recommend **short_review as default**, Oxford
   remains opt-in / router class later.
3. If oxford wins by `Δ < −0.10` overall → keep **debate default**.
   If a single task class shows oxford advantage `> 0.15` with n≥5 in
   that class → routing/opt-in for that class, not a silent default flip.
4. Secondary metrics (USD, tokens, latency, useful_findings, false-positive
   unacceptable claims, justified abstention) are reported. They **must not**
   pick a winner if primary is inconclusive or opposite.

No p-value claim. n=36 is a pilot, not a powered RCT. Approximate power for
McNemar at δ=0.20 is low; treat CIs as descriptive. Several metrics must
not become “whichever won.”

## Frozen arms (development used only to choose, then freeze)

Chosen on development mechanics, not by re-scoring XOR-10 as a winner:

| Arm | Protocol | Role |
|---|---|---|
| `zhoda` | `debate` (Oxford, `rounds_cap` YAML default 4) | current product |
| `short_review` | independent positions → **one** evidence-focused critique → revision → verdict | mandatory ablation; **not** chairman single-pass synthesis |
| `majority` | `vote` | floor: positions, no critique |

Council / self-consistency / best-of-N are **not** in this pilot. Copying
majority into a matched table still requires F resource checks (not used
as the ablation).

## Model / resource policy (intended calls)

At approval the owner freezes **local** `core/zhoda.yaml` (gitignored) via
manifest `config_hash`. Intended shape matches `zhoda.yaml.example`:

- Council: 3 models, judges: 2 outside council, classifiers: 2 distinct
- `clarify_mode=no-clarify`; `--context` = `source_bundle.text` only
- Isolated sqlite per arm; `cache_mode=fresh`; `replicate_id=0`
- Blind judge: first YAML `judges` entry; `judge_overlap` declared; not
  implicit chairman
- Engine usage vs evaluator usage stored separately (F)
- Checkpoint key: `case × arm × replicate × spec_hash`
- Hard **experiment** cap: **$8.00 USD** total across all attempts
  (engine+evaluator). Engine `budget_per_question_usd` remains the per-question
  safety cap from YAML.
- Stop rules: (a) all 36×3 attempts terminal, or (b) spend ≥ $8, or
  (c) `quota_exceeded` / provider hard fail. No “small free sanity run.”
- Failed / ungraded / skipped stay in the denominator (F).

## Holdout protection

- Holdout ids are `evd-*` / `prm-*` / `cfc-*` / `hol-min-*` / `unc-*` /
  `adr-*`. They must not collide with development-51 (`min-001` …).
- Runners load `core/eval/pilot/public.jsonl` only. Gold keys are forbidden
  there. `zhoda_core.eval.pilot` does not import `zhoda_core.eval.gold`.
- Gold is `core/eval/pilot/gold.jsonl`, imported only by scoring / `validate`.
- Protocol prompts are frozen at `bfd7e90`. Do not retune them on this
  holdout’s live outputs. A post-hoc rubric change is a **new version**,
  not a replacement of the primary result.
- Development-51 may still be used for offline regression; it is not this
  study’s sample.

## Scoring (after approval only)

- Blind by arm name. Gold applied after attempts.
- Disputed cases: second annotator, disagreement log, **without** knowing
  which arm won.
- Axes: `action_correct`, `premise_handling`, `constraint_violations`,
  `evidence_support`, `useful_findings`, `appropriate_abstention`.
- Report every case including failures; paired table; uncertainty; false
  positives; evaluator overhead; representative **failures of both**
  oxford and short_review.

## What this preparation is not

- Not a live run. Not independent validation. Not a winner from old XOR-10.
- Not permission to call OpenRouter.

Owner: approve the frozen manifest, spend cap, and arms — or reject.
Without that approval, status stays **READY_FOR_APPROVAL**.
