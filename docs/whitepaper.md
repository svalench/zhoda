# Zhoda: An Open Protocol for Multi-LLM Deliberation

**Whitepaper v0.3 — August 2026**
Alexander Valenchits · [github.com/svalench/zhoda](https://github.com/svalench/zhoda) · zhoda.dev

> *Models argue until they reach zhoda.*

Changelog v0.3: the verdict now renders twice — a report for humans and a
plan contract for cheaper executor agents; claims carry mandatory evidence
fields (unsourced = labeled assumption); the decision tree replaces the
linear chronicle; new metric: dead ends prevented. v0.2: Yes-Brainer and
the 2026 debate-critical analyses in Related Work; honest cost arithmetic;
the honest formula.

## Abstract

Single LLMs hallucinate confidently and almost never ask clarifying questions.
Existing multi-model tools fall into two traps: single-pass councils that collect
parallel answers without revision, and voting schemes that mistake majority for
truth. Zhoda introduces an open deliberation protocol in which models first
interview the user to build a value map, then take positions, self-organize into
factions, and debate Oxford-style — with a rotating devil's advocate and public
faction switches — until they reach consensus (*zhoda*, Belarusian for agreement)
or produce an honest map of dissent. Every verdict ships with a minority report,
a full auditable transcript — and renders twice: a report for humans and a plan
contract for cheaper executor agents. The system is open source (AGPL-3.0), runs
on free OpenRouter models with BYOK, and ships as a core engine, an MCP server,
and a DeepSeek Harness plugin.

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

**The handoff loss.** Even a good decision dies at handoff: the brainstorm
knows why paths were rejected; the cheap executor model doesn't, and walks
into the same dead ends. The value of deliberation evaporates between
"decided" and "done".

## 2. Design principles

1. **Understand before solving.** No answer is produced before the goal,
   success criteria, and constraints are explicit.
2. **Argue in factions, like humans.** Deliberation happens between groups
   formed around positions — not between isolated reviewers.
3. **Zhoda or honest dissent.** When consensus is not reached, the system
   reports a structured disagreement map instead of faking agreement.
4. **Auditability.** Every verdict is reproducible from its transcript.
5. **Cost honesty.** Free models first, explicit budget caps, no hidden spend.
6. **Evidence discipline.** A claim without a source is an opinion — and is
   labeled as one. We never sell the illusion of rigor.

## 3. The Zhoda protocol

### Stage 0 — Elicitation

The council receives the raw question and returns not answers but ambiguities:
`{ambiguity, why_it_matters, candidate_question}`. An aggregator selects the
2–4 questions with the highest decision impact — those whose answers most
diverge the positions. After the user answers, the council is asked again;
the loop continues until remaining ambiguities no longer change the answer
(or a turn cap is hit). The user's answers produce a **value map**:

```
ValueMap { goal, success_criteria[], constraints[], anti_goals[], open_ambiguities[] }
```

Deliberation may pause mid-debate to request clarification: factions discovered
that the verdict depends on an unstated value. Modes: `--no-clarify`,
`--auto-clarify` (no prompts; unanswered items land in `open_ambiguities`,
never marked as facts).

### Stage 1 — Positions

Each model answers with a structured stance, anonymized from the start:

```
Position { thesis, answer, claims[], falsifiability, confidence }
Claim { claim, evidence_url | null, confidence }   # null = labeled "assumption"
```

`falsifiability` — conditions under which the position is wrong — forces
models to define their own failure modes before the debate begins. Every
argument is a Claim: factual statements cite or are honestly marked as
unsourced.

### Stage 2 — Faction formation

Positions are clustered by embedding similarity of theses and arguments
(default merge threshold 0.82). Clusters of 2+ models become **factions** and
produce a platform answer in an internal round; singletons remain independents.
The chairman names factions descriptively ("Pragmatists", "Maximalists").

### Stage 3 — Debate rounds (Oxford-style)

Each round: a faction presents an argument against the strongest opposing
faction → the opponent rebuts → cross-examination obliges the faction to answer
a concrete charge. Critiques are structured and may cite:

```
Critique { target_faction, flaw_type ∈ {factual, logical, scope, values_mismatch},
           claim, evidence_url | null, rebuttal, rebuttal_evidence_url | null }
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
similarity. Adaptive stopping: **unanimous** thesis agreement that persists
`stability_rounds` ends the loop early. Headcount majority does not — the
debate continues to the hard cap (default 4; empirically most debates
converge in 2–3; multi-agent debate literature reports convergence within
4–8 rounds). Majority that held through the cap is honest dissent
(`decision_origin = majority_at_cap`), not zhoda — no plan contract.
Consensus strength:
`unanimous | majority | split | deadlock`. On `split` or `deadlock` with
escalation enabled, the case moves up the model ladder (free → mid → frontier);
the chairman decides from the full transcript.

### Stage 5 — Verdict (renders twice)

```
Verdict {
  decision,                    # majority answer
  zhoda_reached,               # was consensus achieved
  consensus_strength,
  value_map,                   # what the answer was checked against
  minority_report,             # preserved dissent — never erased
  dissent_map[],               # where and why factions disagreed
  switches[],                  # who changed position and why
  decision_tree,               # argument -> what closed it -> who moved
  plan_contract,               # second render: spec for a cheaper executor
  dead_ends_prevented,         # rejected paths that reached plan constraints
  rounds_taken, cost, transcript_id
}
```

**Two renders.** The human report is narrative: decision, risks, minority.
The **plan contract** is for a cheaper executor model: steps with goals,
hard constraints, forbidden paths, and acceptance criteria — nothing left
to inference. Rejected paths are collected programmatically from the
objection ledger and the minority (never invented by the renderer), so the
executor inherits *why* paths died, not just which one survived.

**The metric.** Not "agreement" but **dead ends prevented**: how many
rejected paths made it into the plan's constraints. That is the ROI a
customer buys: invest $2–5 of expensive-model debate once, save hours of
cheap-model execution that no longer walks into discarded dead ends.

## 4. Failure modes and mitigations

| Failure | Mitigation |
|---|---|
| Shared hallucination (correlated errors) | Devil's advocate, anonymization, claim-level structured critique |
| Sycophantic agreement | Obligation to name a concrete flaw type; faction framing creates stakes |
| Endless debate | Adaptive stopping + round cap; escalation ladder as tiebreaker |
| Cost blowup | Per-question budget cap, semantic caching of stages, free-tier-first routing |
| Judge bias | Chairman reads transcripts, not votes; escalation instead of forced synthesis |
| Illusion of rigor | Evidence discipline: unsourced claims are labeled "assumption" everywhere |

## 5. Reputation (planned, post-MVP)

*Not implemented in engine v0.1 — this section is the design, and the moat
argument stands only once the data exists.*

Debate outcomes feed a per-domain ELO rating of models: accepted critiques (+),
confirmed flaws (−), beneficial switches (+). Reputation shapes council
composition per task class and weights votes in consensus. Until a deferred
verification layer exists (opt-in "did this verdict work?" feedback),
reputation measures rhetorical robustness, not truthfulness — and is labeled
accordingly.

## 6. Implementation

Three layers, strict downward dependencies only:

- **zhoda-core** — Python/FastAPI engine: elicitation, factions, debate,
  consensus, verdicts, plan contracts, decision trees. Provider-agnostic;
  OpenRouter first.
- **zhoda-mcp** — Model Context Protocol server (`zhoda_clarify`,
  `zhoda_deliberate`, `zhoda_verdict`, `zhoda_transcript`, `zhoda_reputation`),
  usable from DeepSeek Harness, Claude Code, Codex, any MCP host.
- **@zhoda/dsh-plugin** — DeepSeek Harness plugin: debate room, faction graph
  with animated switches, verdict panel, decision-tree view.

### Cost model — honest arithmetic

A naive estimate (2N+1 per stage) suggests ~21 requests for a 5-model,
3-round deliberation. The real engine does more: pairwise faction formation,
council-wide elicitation, two judges per closure, revisions, supersede checks.
Honest formula:

```
requests ≈ router (2) + elicitation (N) + positions (N)
         + faction formation (≤ N(N−1)/2 pairwise + synthesis + naming)
         + per round: F critiques + 1 devil's advocate + R rebuttals
           + 2 closure votes per open objection + revisions
           + supersede checks + switch prompts + 2 consensus votes
         + plan-contract render (1)
```

For a 4-model council, 2 factions, 2 rounds: **typically 35–50 requests, up
to ~60 worst case**. Free-tier reality (20 req/min, 50 req/day — 1000/day
after a one-time $10 credit): that is 1–2 full deliberations per day at
50/day, or 20–25 at 1000/day. Two consequences:

1. **Zhoda is a second-opinion instrument for rare, expensive questions —
   not a chat loop.** The protocol router exists precisely so simple
   questions never pay debate prices (the vote path costs ≈ 2 + 2N + ≤N²/2).
2. **Cost cutting is protocol-level, not cosmetic:** the cheap vote path,
   stage caching (persistent sqlite), early stopping on stable consensus,
   the pairwise prefilter, and escalation only on deadlock. On cheap paid
   models the same 50 calls cost cents — the binding constraint is free-tier
   rate limits, not dollars. Every verdict carries a per-stage request
   breakdown so costs stay debuggable.

## 7. Economics

Zhoda is open source under a split license with donation-based funding
(GitHub Sponsors, OpenCollective, crypto): Apache-2.0 for the engine,
MCP server, and plugins; AGPL-3.0 for a hosted API server (when it exists).
See [LICENSE.md](../LICENSE.md). There is deliberately **no token**: DePIN
precedents show that compute tokens without pre-existing demand collapse
(−70–85% from ATH across the sector). If a distributed inference network ever
emerges around Zhoda, it will start with credit-style accounting
(BitTorrent-ratio-like), not a tradable asset.

The pricing frame is the price of complexity, not the price of dialogue: the
real competitor is a human arguing with themselves and a chatbot for two
hours, badly, forgetting half the arguments. Zhoda sells: "invest $2–5 of
expensive-model debate once — save hours of cheap-model execution, because
the plan already accounted for every dead end."

## 8. Evaluation plan

1. **Benchmarks:** Zhoda debate vs majority-without-debate (vote protocol),
   single-pass council, and self-consistency / best-of-N. Self-consistency
   votes on a structured `answer` field, not full-text equality. Open-ended
   self-consistency spends `max(C-1, 1)` samples + 1 cluster judge (same
   budget as best-of-N). Two tables:
   compute-matched (same API-call count) and cost-matched (same USD, or
   total tokens when USD is 0). Request count is not cost. Infrastructure is in
   `zhoda_core.benchmarks`; numbers land here only after a measured run.
2. **Metrics:** accuracy, calibration (stated confidence vs correctness),
   dissent usefulness (human-rated), cost per correct answer, dead ends
   prevented per dollar.
3. **Open dataset:** opt-in anonymized debate transcripts for the research
   community.

## 9. Related work

- **llm-council** (Karpathy, 2025) — single-pass council: answer, rank,
  synthesize. No revision, no factions.
- **Yes-Brainer** (Trekhleb, July 2026) — the closest neighbor, and proof the
  UX demand is real: a browser-only BYOK council with three modes — parallel
  answers, anonymized peer vote + judge, and a multi-round consensus debate
  with reshuffled aliases and a mediator that either converges or honestly
  reports what stayed contested. It ships a live demo today. It is a
  human-facing web app without a backend: no factions, no objection lifecycle,
  no anti-capitulation mechanics (participants re-answer freely each round,
  but nothing tracks *why* a position moved), no conflict-of-interest handling
  for its judge/mediator, no elicitation stage, no agent-harness surface —
  and no machine-readable plan for a cheaper executor.
- **Multi-agent debate** (Du et al., 2023) — iterative convergence; Zhoda adds
  elicitation, factions, structured critique, and dissent preservation.
- **Mixture-of-Agents** (Together, 2024) — layered synthesis without
  adversarial rounds.
- **ChatEval** — debate as an evaluator; Zhoda is debate as a solver.
- **Debate or Vote** (NeurIPS 2025) — protocol choice is task-dependent;
  Zhoda routes the protocol per task class.
- **MARE / requirements-elicitation agents** — academic elicitation; Zhoda
  productizes it as Stage 0 for any question.
- **The Consistency Illusion** (2026) and **Emergence of Biased Consensus**
  (2026) — recent analyses caution that standard multi-agent debate may not
  improve answer accuracy on its own and can amplify shared bias into
  confident consensus. Zhoda's answer is structural, not assumed:
  anti-capitulation mechanics, conflict-free judging — and a benchmark from
  day one (§8), because gains must be measured, not declared.

**The honest formula, as of August 2026:** Zhoda is the protocol where a model
may not switch sides without an unclosed objection, agreement does not count
until it survives two consecutive rounds, and every verdict carries the
minority and the chronicle — plus a plan contract that tells a cheaper
executor which paths died and why. Iterative multi-model debate exists
(Yes-Brainer, the MAD lineage); agent-infrastructure protocols with an
auditable trust surface do not.

## 10. Roadmap

See `docs/master-plan.md`. Beachhead wedge: architecture and product decision
verdicts inside agent harnesses (DeepSeek Harness, Claude Code via MCP), and
plan reviews in the IDE. Explicitly not medicine, finance, or compliance
theater — regulated domains would eat the project. Near term: core MVP on
free models, then the MCP server, then the dsh plugin.

## References

1. Karpathy, A. `llm-council`. github.com/karpathy/llm-council (2025)
2. Trekhleb, O. `yesbrainer`. github.com/trekhleb/yesbrainer, yesbrainer.ai (2026)
3. Du, Y. et al. *Improving Factuality and Reasoning in Language Models through
   Multiagent Debate* (2023)
4. Together AI. *Mixture-of-Agents* (2024)
5. Chan, C.-M. et al. *ChatEval: Towards Better LLM-based Evaluators through
   Multi-Agent Debate* (ICLR 2024)
6. *Debate or Vote: Which Yields Better Decisions in Multi-Agent LLMs?*
   (NeurIPS 2025)
7. *The Consistency Illusion: How Multi-Agent Debate Hides Reasoning
   Misalignment* (2026); Okawa, M. *Emergence of Biased Consensus in
   Multi-Agent LLM Debate* (2026)
8. OpenRouter API limits — openrouter.ai/docs
