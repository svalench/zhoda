# Live Run — the first real deliberation

Everything so far was scripted tests. This runbook takes the engine to real
OpenRouter models. Bring your own key. Cheap paid IDs (no `:free` suffix) are
the stable default; `budget_per_question_usd: 0` still locks the run to `:free`.

## 1. Prerequisites

- Python 3.12+
- An OpenRouter account and API key: https://openrouter.ai/keys
- Paid cheap models need credits on the key. `:free` IDs ignore those credits
  and hit provider 429s (Google AI Studio, Poolside, …). Free-tier caps if you
  stay on `:free`: 20 requests/min, 50 requests/day (1000/day after a one-time
  $10 credit). A 3-model debate costs ~15–50 requests.

```bash
# repo root or core/.env — the CLI walks up to the git root
echo 'OPENROUTER_API_KEY=sk-or-...' > .env
# or: export OPENROUTER_API_KEY=sk-or-...
```

## 2. Install and self-check

```bash
git clone https://github.com/svalench/zhoda && cd zhoda
cd core
uv sync                 # creates core/.venv; do not mix with the repo-root .venv
uv run pytest -m "not live"   # the whole suite is scripted — zero API spend
```

zsh treats `[dev]` as a glob — never `pip install -e core/[dev]`. From the repo
root, quote the extra: `pip install -e './core[dev]'`. Prefer `uv sync` in `core/`
so the CLI lives in `core/.venv`, not the unused repo-root `.venv`.

If the suite is green, the install is sound. Do not skip this: a broken
install discovered mid-debate wastes your daily request budget.

## 3. Configure zhoda.yaml

Keep council and judges disjoint (the engine refuses to start otherwise —
that is deliberate, round-9 §2). Cheap paid IDs from three labs; verify at
https://openrouter.ai/models. `budget: 0` + `:free` suffix still works if you
want a zero-dollar run.

```yaml
council:                        # 3 debaters (cheap paid — not :free)
  - openai/gpt-4.1-mini
  - google/gemini-2.5-flash-lite
  - deepseek/deepseek-v4-flash
judges:                         # 2 models OUTSIDE the council
  - openai/gpt-4o-mini
  - google/gemini-3.1-flash-lite
router_classifiers:             # two distinct cheap models
  - openai/gpt-4o-mini
  - google/gemini-3.1-flash-lite
chairman: openai/gpt-4.1-mini
rounds_cap: 4
stability_rounds: 2
devils_advocate: true
ambiguity_threshold: 0.6
max_new_per_round: 3
max_active: 6
budget_per_question_usd: 10.0   # hard cap per question; 0 = :free only
max_concurrency: 8
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
cd core
uv run zhoda deliberate "PostgreSQL or Kafka for a 50k RPS ledger, team of four?"
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
- cost: requests, USD, per-stage breakdown, transcript ID

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
- HTTP 429 on `:free` IDs — provider free-endpoint cap, not missing credits;
  drop the `:free` suffix (needs `budget_per_question_usd > 0`) or wait
- HTTP 429 on paid IDs — lower `max_concurrency`, wait a minute, re-run
  (cached stages are free)
- `all council models failed` — a model went down or returned prose
  instead of JSON; swap its ID
- A model ID 404s — IDs rotate; pick a current one from the model list

## What to collect

Keep every transcript. Zhoda rate, rounds distribution, switch rate,
paths_rejected, and cost per question are the evaluation dataset —
section 8 of the whitepaper starts here.
