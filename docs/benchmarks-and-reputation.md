# Benchmarks and Domain-Weighted Reputation

Two subsystems from the evaluation plan: a robustness benchmark suite
(`zhoda_core.benchmarks`) and a per-domain reputation model
(`zhoda_core.reputation`) feeding consensus weights in Stage 4.

## Reputation model

Each model holds a sparse per-domain ELO row. For a question with domain
vector `p` (produced during Stage 0 elicitation):

    R_eff(m) = sum_k p_k * ELO[m, k]

Vote weight in consensus:

    w_m = softmax(R_eff(m) / tau),   tau = 400 by default

A floor (`MIN_WEIGHT = 0.05`) keeps every faction audible. Post-debate
updates scale K by the domain vector, so a security debate moves mostly
the security coordinate:

| Event | Delta |
|---|---|
| Critique accepted | +16 * magnitude |
| Flaw confirmed | -16 * magnitude |
| Beneficial switch | +8 * magnitude |

Storage: atomic JSON at `$ZHODA_REPUTATION_PATH` or `~/.zhoda/reputation.json`.

## Benchmark suites

| Suite | Kind | What it measures |
|---|---|---|
| sycophancy | biased_premise | Question embeds a false premise; system must surface it in Stage 0 or refute it |
| sycophancy | bandwagon | Injected weak agents form a wrong majority; measures echo-chamber flips |
| minority | true_minority | Obvious majority answer is wrong; one model holds provable truth |

### Arms (compare)

### Matching tables

`--mode compare` runs Zhoda first, then publishes **two independent tables**.
`CaseResult` records `requests`, `input_tokens`, `output_tokens`,
`total_tokens`, `usd`, `latency_s`, `cache_hits` from the provider delta
(`CostReport`). Request count is a proxy, not cost: a long chairman
prompt can dwarf a short sample even at the same C.

| Table | Budget | How baselines spend it |
|---|---|---|
| `compute_matched` | `C = max(zhoda.requests, 1)` API calls | `self_consistency` / `council` use C samples; open-ended SC may add 1 cluster judge. `best_of_n` uses `max(C-1, 1)` gens + 1 pick judge |
| `cost_matched` | `zhoda.usd` if > 0, else `zhoda.total_tokens` | padable arms sample until the spend is met (may overshoot by one call / the judge). `majority` is a protocol run, not padded |

`majority` and `zhoda` appear in both tables with the same spend (not padded).

| Mode | What it does |
|---|---|
| `zhoda` | `ZhodaEngine` with `force_protocol=debate`, `clarify_mode=no-clarify` |
| `majority` | `ZhodaEngine` with `force_protocol=vote` (positions + classify, no debate rounds) |
| `council` | each model answers once; chairman synthesizes (Karpathy-style, no factions). Extra budget → more samples of the first model |
| `self_consistency` | JSON `{answer, confidence, reason}`; majority on `answer` (options if set). Open-ended: one blind cluster judge when labels diverge |
| `best_of_n` | generations + 1 judge (`max(C-1, 1)+1` in the compute table) |

`--dry-run` uses deterministic mock profiles (no API). Live runs need
`zhoda.yaml` and `OPENROUTER_API_KEY`; a missing key or YAML is exit 2,
never a silent mock.

### Metrics

- **Resistance rate** — share of biased-premise cases where the premise was challenged.
- **Sycophancy flip rate** — share of bandwagon cases where the verdict adopted the injected majority.
- **Minority preservation rate** — share of true-minority cases where the truth survived in `minority_report`.
- **Convincing power** — share of true-minority cases with >= 1 faction switch toward the minority.
- **Brier score** — calibration of reported confidence vs correctness.
- **avg_requests** — mean provider calls (compute proxy).
- **avg_input_tokens / avg_output_tokens / avg_total_tokens / avg_usd /
  avg_latency_s / avg_cache_hits** — actual spend; compare these in the
  cost-matched table, not request count.

## CLI

```bash
# offline pipeline validation on deterministic mock profiles
python -m zhoda_core.benchmarks run --suite all --mode compare --dry-run --out report.json

# live run (BYOK, council YAML)
python -m zhoda_core.benchmarks run --suite sycophancy \
    --config zhoda.yaml --clarify no-clarify --mode compare --out report.json

# isolated baseline with an explicit sample budget
python -m zhoda_core.benchmarks run --suite sycophancy \
    --mode self_consistency --n-samples 8

python -m zhoda_core.benchmarks export-reputation --output domain_weights.json
```

Architecture-rubric seeds live in `core/tests/bench/tasks.jsonl` (pass via
`--dataset` after converting to `BenchmarkCase` JSONL).

## Integration points

- `classify_domains(question)` is called during Stage 0; the resulting
  vector rides along the ValueMap into Stage 4 consensus and the verdict.
- `DomainEloMatrix.vote_weights(models, vector)` replaces uniform voting
  when computing consensus strength.
- Debate outcomes (`CritiqueAccepted`, `FlawConfirmed`, `FactionSwitch`)
  map to `ReputationEvent` records persisted after each verdict.
- `HeuristicJudge` is a placeholder; production runs should plug an LLM
  judge implementing the same `evaluate(case, outcome, mode)` signature.

## Extending datasets

Datasets are JSONL; see `BenchmarkCase` in
`core/src/zhoda_core/benchmarks/datasets.py`. Generate variants with
`dump_cases`, edit, and pass via `--dataset`.
