# Zhoda: An Open Protocol for Multi-LLM Deliberation

**Whitepaper v0.1 — August 2026**
Alexander Valenchits · [github.com/svalench/zhoda](https://github.com/svalench/zhoda) · zhoda.dev

> *Models argue until they reach zhoda.*

## Abstract

Single LLMs hallucinate confidently and almost never ask clarifying questions.
Existing multi-model tools fall into two traps: single-pass councils that collect
parallel answers without revision, and voting schemes that mistake majority for
truth. Zhoda introduces an open deliberation protocol in which models first
interview the user to build a value map, then take positions, self-organize into
factions, and debate Oxford-style — with a rotating devil's advocate and public
faction switches — until they reach consensus (*zhoda*, Belarusian for agreement)
or produce an honest map of dissent. Every verdict ships with a minority report
and a full auditable transcript. The system is open source (AGPL-3.0), runs on
free OpenRouter models with BYOK, and ships as a core engine, an MCP server, and
a DeepSeek Harness plugin.

## 1. Problem

**Confident fabrication.** LLMs prefer a plausible answer to a clarifying
question. Most wrong answers are answers to a wrongly understood question —
the model jumps to a solution before the problem is specified.

**Single-pass councils.** Tools like `llm-council` collect one answer per model,
let models rank each other once, and synthesize. No model ever revises its
position in light of criticism — evaluation without deliberation.

**The voting fallacy.** Majority voting assumes independent errors. LLMs share
training data, RLHF biases, and blind spots — errors are correlated, so a
majority can converge on a confidently wrong answer.

**Sycophancy.** When models see each other's answers, they tend to agree rather
than attack weak claims. Debate without an obligation to find concrete flaws
degenerates into politeness.

## 2. Design principles

1. **Understand before solving.** No answer is produced before the goal,
   success criteria, and constraints are explicit.
2. **Argue in factions, like humans.** Deliberation happens between groups
   formed around positions — not between isolated reviewers.
3. **Zhoda or honest dissent.** When consensus is not reached, the system
   reports a structured disagreement map instead of faking agreement.
4. **Auditability.** Every verdict is reproducible from its transcript.
5. **Cost honesty.** Free models first, explicit budget caps, no hidden spend.

## 3. The Zhoda protocol

### Stage 0 — Elicitation

The council receives the raw question and returns not answers but ambiguities:
`{ambiguity, why_it_matters, candidate_question}`. An aggregator selects the
2–4 questions with the highest decision impact — those whose answers most
diverge the positions. The user's answers produce a **value map**:

```
ValueMap { goal, success_criteria[], constraints[], anti_goals[], open_ambiguities[] }
```

Deliberation may pause mid-debate to request clarification: factions discovered
that the verdict depends on an unstated value. Modes: `--no-clarify`,
`--auto-clarify` (assumptions are generated and explicitly marked).

### Stage 1 — Positions

Each model answers with a structured stance, anonymized from the start:

```
Position { thesis, answer, arguments[], falsifiability, confidence }
```

`falsifiability` — conditions under which the position is wrong — forces
models to define their own failure modes before the debate begins.

### Stage 2 — Faction formation

Positions are clustered by embedding similarity of theses and arguments
(default merge threshold 0.82). Clusters of 2+ models become **factions** and
produce a platform answer in an internal round; singletons remain independents.
The chairman names factions descriptively ("Pragmatists", "Maximalists").

### Stage 3 — Debate rounds (Oxford-style)

Each round: a faction presents an argument against the strongest opposing
faction → the opponent rebuts → cross-examination obliges the faction to answer
a concrete charge. Critiques are structured:

```
Critique { target_faction, flaw_type ∈ {factual, logical, scope, values_mismatch}, claim, rebuttal }
```

A rotating **devil's advocate** must attack the currently leading position —
structural protection against sycophantic convergence.

**Faction switches.** A model may publicly join another faction, citing the
argument that convinced it:

```
FactionSwitch { model, from_faction, to_faction, convinced_by }
```

Switching under the force of an argument is the protocol's core truth-seeking
signal — and its most compelling artifact in the transcript.

### Stage 4 — Consensus (zhoda)

Convergence is computed on structured theses after each round, never on prose
similarity. Adaptive stopping: consensus ends the loop early; hard cap at 4
rounds (empirically most debates converge in 2–3; multi-agent debate literature
reports convergence within 4–8 rounds). Consensus strength:
`unanimous | majority | split | deadlock`. On `split` or `deadlock` with
escalation enabled, the case moves up the model ladder (free → mid → frontier);
the chairman decides from the full transcript.

### Stage 5 — Verdict

```
Verdict {
  decision,                    # majority answer
  zhoda_reached,               # was consensus achieved
  consensus_strength,
  value_map,                   # what the answer was checked against
  minority_report,             # preserved dissent — never erased
  dissent_map[],               # where and why factions disagreed
  switches[],                  # who changed position and why
  rounds_taken, cost, transcript_id
}
```

## 4. Failure modes and mitigations

| Failure | Mitigation |
|---|---|
| Shared hallucination (correlated errors) | Devil's advocate, anonymization, claim-level structured critique |
| Sycophantic agreement | Obligation to name a concrete flaw type; faction framing creates stakes |
| Endless debate | Adaptive stopping + round cap; escalation ladder as tiebreaker |
| Cost blowup | Per-question budget cap, semantic caching of stages, free-tier-first routing |
| Judge bias | Chairman reads transcripts, not votes; escalation instead of forced synthesis |

## 5. Reputation

Debate outcomes feed a per-domain ELO rating of models: accepted critiques (+),
confirmed flaws (−), beneficial switches (+). Reputation shapes council
composition per task class and weights votes in consensus. Over time this
becomes a dataset about model trustworthiness by domain — a moat that no
single-pass tool accumulates.

## 6. Implementation

Three layers, strict downward dependencies only:

- **zhoda-core** — Python/FastAPI engine: elicitation, factions, debate,
  consensus, verdicts, reputation. Provider-agnostic; OpenRouter first.
- **zhoda-mcp** — Model Context Protocol server (`zhoda_clarify`,
  `zhoda_deliberate`, `zhoda_verdict`, `zhoda_transcript`, `zhoda_reputation`),
  usable from DeepSeek Harness, Claude Code, Codex, any MCP host.
- **@zhoda/dsh-plugin** — DeepSeek Harness plugin: debate room, faction graph
  with animated switches, verdict panel.

Cost model: a 5-model, 3-round deliberation costs ~21 requests. OpenRouter free
tier allows 20 req/min and 50 req/day (1000/day after a one-time $10 credit
purchase) — sufficient for daily personal use with BYOK and caching; the
escalation ladder turns free models into the first instance, not the ceiling.

## 7. Economics

Zhoda is open source under AGPL-3.0 with donation-based funding (GitHub
Sponsors, OpenCollective, crypto). There is deliberately **no token**: DePIN
precedents show that compute tokens without pre-existing demand collapse
(−70–85% from ATH across the sector). If a distributed inference network ever
emerges around Zhoda, it will start with credit-style accounting
(BitTorrent-ratio-like), not a tradable asset.

## 8. Evaluation plan

1. **Benchmarks:** single model vs single-pass council vs Zhoda on reasoning
   and decision-quality tasks; factuality suites.
2. **Metrics:** accuracy, calibration (stated confidence vs correctness),
   dissent usefulness (human-rated), cost per correct answer.
3. **Open dataset:** opt-in anonymized debate transcripts for the research
   community.

## 9. Related work

- **llm-council** (Karpathy, 2025) — single-pass council: answer, rank,
  synthesize. No revision, no factions.
- **Multi-agent debate** (Du et al., 2023) — iterative convergence; Zhoda adds
  elicitation, factions, structured critique, and dissent preservation.
- **Mixture-of-Agents** (Together, 2024) — layered synthesis without
  adversarial rounds.
- **ChatEval** — debate as an evaluator; Zhoda is debate as a solver.
- **Debate or Vote** (NeurIPS 2025) — protocol choice is task-dependent;
  Zhoda routes the protocol per task class.
- **MARE / requirements-elicitation agents** — academic elicitation; Zhoda
  productizes it as Stage 0 for any question.

None combine clarification-first, factional debate, honest dissent, and
reputation. That combination is Zhoda.

## 10. Roadmap

See `docs/master-plan.md`. Near term: core MVP (elicitation → factions →
2 rounds → verdict) on free models, then the MCP server, then the dsh plugin.

## References

1. Karpathy, A. `llm-council`. github.com/karpathy/llm-council (2025)
2. Du, Y. et al. *Improving Factuality and Reasoning in Language Models through
   Multiagent Debate* (2023)
3. Together AI. *Mixture-of-Agents* (2024)
4. Chan, C.-M. et al. *ChatEval: Towards Better LLM-based Evaluators through
   Multi-Agent Debate* (ICLR 2024)
5. *Debate or Vote: Which Yields Better Decisions in Multi-Agent LLMs?*
   (NeurIPS 2025)
6. OpenRouter API limits — openrouter.ai/docs
