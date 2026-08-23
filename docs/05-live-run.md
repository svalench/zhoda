# Live Run — the first real deliberation

Everything so far was scripted tests. This runbook takes the engine to real
free-tier OpenRouter models. Bring your own key; spend nothing.

## 1. Prerequisites

- Python 3.11+
- An OpenRouter account and API key: https://openrouter.ai/keys
- Free tier: 20 requests/min, 50 requests/day (1000/day after a one-time
  $10 credit purchase). A 3-model, 1-round debate costs ~15–25 requests —
  plan for one or two deliberations per day on the bare free tier.

```bash
export OPENROUTER_API_KEY=sk-or-...
```

## 2. Install and self-check

```bash
git clone https://github.com/svalench/zhoda && cd zhoda
pip install -e core/[dev]
(cd core && pytest)   # the whole suite is scripted — zero API spend
```

If the suite is green, the install is sound. Do not skip this: a broken
install discovered mid-debate wastes your daily request budget.

## 3. Configure zhoda.yaml

Free model IDs rotate — pick CURRENT `:free` variants from
https://openrouter.ai/models?q=free and keep council and judges disjoint
(the engine refuses to start otherwise — that is deliberate, round-9 §2):

```yaml
council:                        # 3 debaters (example IDs — verify availability)
  - deepseek/deepseek-chat-v3-0324:free
  - qwen/qwen3-235b-a22b:free
  - meta-llama/llama-3.3-70b-instruct:free
judges:                         # 2 models OUTSIDE the council
  - mistralai/mistral-small-3.1-24b-instruct:free
  - google/gemma-3-27b-it:free
router_classifiers:             # two distinct cheap models
  - mistralai/mistral-small-3.1-24b-instruct:free
  - google/gemma-3-27b-it:free
chairman: deepseek/deepseek-chat-v3-0324:free
rounds_cap: 4
stability_rounds: 2
devils_advocate: true
ambiguity_threshold: 0.6
max_new_per_round: 3
max_active: 6
budget_per_question_usd: 0.0    # free models only — hard guarantee
max_concurrency: 4              # stay under the 20 req/min free cap
cache_path: .zhoda-cache.json   # re-runs of a stage are free
transcripts_dir: transcripts
escalation:
  enabled: false                # opt-in; an appeal is a labeled fiat, not zhoda
  model: null
```

## 4. The first question

Start with a real decision you care about — the protocol is built for
decisions, not trivia:

```bash
zhoda deliberate "PostgreSQL or Kafka for a 50k RPS ledger, team of four?"
```

In `smart` mode the council may interview you first — answer the questions;
they become the value map the debate is checked against. For a hands-off run:
`--clarify no-clarify`.

## 5. Read the verdict

The CLI prints, in order:

- `zhoda_reached` + strength + rounds — split/deadlock is an honest outcome,
  not a failure
- `APPELLATE DECISION without consensus` in red — only if escalation fired
- the decision, the minority report (preserved dissent), switches
- `paths rejected` — what a REACHED consensus rejected; on clean unanimity:
  "nothing was disputed, nothing to reject"
- the plan contract (rendered ONLY on zhoda) — hand it to a cheaper executor
- cost: requests, USD (should be $0.0000), per-stage breakdown, transcript ID

Then audit: `transcripts/<id>.jsonl` reproduces every stage. In the
decision tree, check the evidence labels — anything a model named from
memory is `unverified_claim`, never `sourced`.

## 6. What to try next

- A factual question → the router should pick `vote` (0 rounds, cheap)
- A code snippet review → `red_team`: the devil's advocate attacks even a
  unanimous "looks fine"
- The same question twice → the second run is served from cache (watch
  `cache_hits`)

## Troubleshooting

- `no judges configured` / `judges must sit OUTSIDE the council` — fix
  zhoda.yaml; the engine is refusing on purpose
- HTTP 429 — you hit the free rate cap; lower `max_concurrency`, wait a
  minute, re-run (cached stages are free)
- `all council models failed` — a free model went down or returned prose
  instead of JSON; swap its ID for a current `:free` variant
- A model ID 404s — free IDs rotate; pick a fresh one from the model list

## What to collect

Keep every transcript. Zhoda rate, rounds distribution, switch rate,
paths_rejected, and cost per question are the evaluation dataset —
section 8 of the whitepaper starts here.
