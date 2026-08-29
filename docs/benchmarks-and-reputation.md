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

### Metrics

- **Resistance rate** — share of biased-premise cases where the premise was challenged.
- **Sycophancy flip rate** — share of bandwagon cases where the verdict adopted the injected majority.
- **Minority preservation rate** — share of true-minority cases where the truth survived in `minority_report`.
- **Convincing power** — share of true-minority cases with >= 1 faction switch toward the minority.
- **Brier score** — calibration of reported confidence vs correctness.

Every verdict is compared three ways: single model, single-pass council,
full Zhoda deliberation.

## CLI

```bash
# offline pipeline validation on deterministic mock profiles
python -m zhoda_core.benchmarks run --suite all --mode compare --dry-run --out report.json

# real run (requires zhoda-core engine wiring)
python -m zhoda_core.benchmarks run --suite sycophancy \
    --models "meta-llama/llama-3.3-70b-instruct:free,deepseek/deepseek-chat-v3-0324:free" \
    --rounds 3 --mode compare

python -m zhoda_core.benchmarks export-reputation --output domain_weights.json
```

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
