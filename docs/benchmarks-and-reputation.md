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
| decision | xor | 51-case mix (architecture / security / ops XOR + the seed suites). A dissent map that names both options is a miss (`foil_keywords`) |

`--suite decision` loads `core/eval/bench/decision-50.jsonl` (also `builtin_cases("decision")`). Slice with `--limit`, `--offset`, `--kind xor`.

Headline for the MVP gate: **LLM committed accuracy** on `xor` plus
**dead_ends_per_usd**. Keyword accuracy saturates on textbook XOR.
`dead_ends` is `len(verdict.paths_rejected)` — empty unless zhoda was
reached (split/majority-at-cap is not a rejection). Majority at cap is
still not zhoda; `decision` is a labeled leading thesis plus dissent.

### Arms (compare)

### Matching tables

`--mode compare` runs Zhoda first, then publishes **two independent tables**.
`CaseResult` records `requests`, `input_tokens`, `output_tokens`,
`total_tokens`, `usd`, `latency_s`, `cache_hits` from the provider delta
(`CostReport`). Request count is a proxy, not cost: a long chairman
prompt can dwarf a short sample even at the same C.

| Table | Budget | How baselines spend it |
|---|---|---|
| `compute_matched` | `C = max(zhoda.requests, 1)` API calls | discrete SC / council: C samples. Open-ended SC and `best_of_n`: `max(C-1, 1)` gens + 1 judge |
| `cost_matched` | `zhoda.usd` if > 0, else `zhoda.total_tokens` | padable arms sample with a pre-check (next estimated call must not reach the cap; may undershoot). `majority` is a protocol run, not padded |

`majority` and `zhoda` appear in both tables with the same spend (not padded).

| Mode | What it does |
|---|---|
| `zhoda` | `ZhodaEngine` with `force_protocol=debate`, `clarify_mode=no-clarify` |
| `majority` | `ZhodaEngine` with `force_protocol=vote` (positions + classify, no debate rounds) |
| `council` | each model answers once; chairman synthesizes (Karpathy-style, no factions). Extra budget → more samples of the first model |
| `self_consistency` | JSON `{answer, confidence, reason}`; majority on `answer` (options if set). Open-ended: `max(C-1, 1)` samples + 1 cluster judge when 1 < unique ≤ 24 |
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
  avg_cache_hits** — actual spend; compare these in the cost-matched table,
  not request count. Do **not** compare `avg_latency_s` across matching
  tables: cost-mode sampling is sequential (stop on budget), compute-mode
  is `asyncio.gather`.
- **avg_json_parse_rate** — share of SC samples that parsed as
  `{answer, ...}` JSON (1.0 = all structured). Free models that ignore JSON
  degrade toward full-text voting; this rate flags that.
- **avg_dead_ends** — mean `len(paths_rejected)` (0 at split / majority-at-cap).
- **dead_ends_per_usd** — sum of those counts / sum of USD. The ROI
  "dead ends prevented" still waits on executor feedback; this is the
  honest programmatic proxy.
- **zhoda_rate** — share of cases with `zhoda_reached`.

`--arms zhoda,majority,council` and `--tables compute` skip SC/BoN and the
cost-matched table (less spend). Live runs default `--models` to the YAML
council, not the free-tier CLI default. `--rounds` is the engine
`rounds_cap` for Zhoda/majority arms.

`HeuristicJudge` is keyword-blind (it does not see the arm name). It is
not an LLM judge; a human subsample of live decisions lives in
`docs/live-runs/`.

### First measured slice (2026-09-05)

16 XOR architecture cases, arms zhoda / majority / council, compute-matched.
Report: [live-runs/2026-09-05-bench.md](live-runs/2026-09-05-bench.md).

| arm | accuracy | zhoda_rate | dead_ends/$ | avg_usd |
|---|---|---|---|---|
| zhoda | 16/16 | 1/16 | 8.0 | $0.0078 |
| majority | 16/16 | 16/16 | 146 | $0.0013 |
| council | 15/16 | — | 0 | $0.0047 |

Wall 35.7 min, **$0.220**. Council miss: Kafka on the 50k RPS ledger
(zhoda/vote: PostgreSQL). Keyword accuracy is saturated on textbook XOR;
the protocol gap is zhoda_rate (debate mostly majority_at_cap).

Blind LLM rescore of the same 16 (committed pick, arm hidden):
zhoda **1/16**, majority 15/16, council 15/16.

Second live slice (xor `--offset 16`, isolated caches, live LLM judge,
39.3 min, **$0.265**): zhoda **2/16**, majority 15/16, council 14/16.
Majority now 10 req / 0 cache hits (the shared-cache leak is closed).

Pivot slice (new `decision` format, xor `--offset 1 --limit 6`, 14.9 min,
**$0.094**): zhoda **5/6** LLM committed, zhoda_rate **0/6**, majority 6/6,
council 6/6. Same six cases were 0/6 under the old dissent map. Miss:
`arch-monolith` (leading thesis = microservices).

Rest-19 (`--offset 32`, last 4 XOR + 15 syc/min, 40.4 min, **$0.274**):
zhoda **17/19**, majority 18/19, council 18/19. Ops XOR-4: 4/4 all arms.
Pre-guard new-protocol XOR **9/10 vs council 10/10**. Debate syc misses
on that slice: `syc-001` accepted REST-faster; `syc-006` recommended
root-on-metal.

XOR-10 post-guard (same 10 XOR, loaded-premise + XOR-pick refusal,
26.4 min, **$0.144**): zhoda **10/10** LLM committed, majority **9/10**,
council **10/10**. `zhoda_rate` 0/10. Gate Zhoda ≥ council holds.
`arch-monolith` rec is Monolith. Majority miss: `ops-friday` hedge.

## CLI

```bash
# offline pipeline validation on deterministic mock profiles
python -m zhoda_core.benchmarks run --suite all --mode compare --dry-run --out report.json

# 51-case decision suite, dry-run
python -m zhoda_core.benchmarks run --suite decision --mode compare --dry-run --quiet

# live XOR slice vs majority + council (isolated caches, compute-matched)
python -m zhoda_core.benchmarks run --suite decision --kind xor --limit 16 \
    --mode compare --arms zhoda,majority,council --tables compute \
    --config zhoda.yaml --clarify no-clarify --rounds 4 --judge llm \
    --cache-path .zhoda-cache-bench.db --out report.json

# blind-LLM rescore of a saved report (does not re-run arms)
python -m zhoda_core.benchmarks rescore \
    --report report.json --config zhoda.yaml --out report-llm.json

# isolated baseline with an explicit sample budget
python -m zhoda_core.benchmarks run --suite sycophancy \
    --mode self_consistency --n-samples 8

python -m zhoda_core.benchmarks export-reputation --output domain_weights.json
```

Architecture-rubric seeds also live in `core/tests/bench/tasks.jsonl`.
The scored decision set is `core/eval/bench/decision-50.jsonl`.

## Integration points

- `classify_domains(question)` is called during Stage 0; the resulting
  vector rides along the ValueMap into Stage 4 consensus and the verdict.
- `DomainEloMatrix.vote_weights(models, vector)` replaces uniform voting
  when computing consensus strength.
- Debate outcomes (`CritiqueAccepted`, `FlawConfirmed`, `FactionSwitch`)
  map to `ReputationEvent` records persisted after each verdict.
- `HeuristicJudge` is the keyword overlay. `--judge llm` / `rescore`
  uses `BlindLlmJudge`: committed gold pick, arm name hidden. Dissent
  maps score as a miss even if the gold label appears first.

## Extending datasets

Datasets are JSONL; see `BenchmarkCase` in
`core/src/zhoda_core/benchmarks/datasets.py`. Generate variants with
`dump_cases`, edit, and pass via `--dataset`.
