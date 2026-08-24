# Critique & Response

The project was hardened through eleven rounds of hostile review. Each round
lists the critique and the fix that landed.

## Round 1 — the whitepaper

- *Voting fallacy is asserted, not shown.* → Related work: correlated errors
  are the documented default; the burden of proof is on independence.
- *No related work.* → Section added: single-pass councils, MAD, society of
  minds, voting ensembles, spec-driven development.
- *"No token" is ideology.* → Economics: DePIN precedents, credit-style
  accounting rationale.

## Round 2 — the core spec

- *Embeddings for clustering are a cost lie.* → Judge-based clustering, two
  models outside the council.
- *Chairman is a single point of failure.* → Chairman only names factions;
  verdicts are built programmatically.
- *Switches are unverifiable.* → Validation: open objection by ID, non-empty
  citation, target is the objection's author faction.

## Round 3 — the MCP server

- *`zhoda_deliberate` blocks the host.* → Async job model: submit, poll,
  fetch.
- *No auth story.* → BYOK, per-key budget caps, no server-held funds.

## Round 4 — the plugin

- *Animated switches are theater.* → The switch graph is the audit view:
  every edge cites the objection that caused it.
- *DeepSeek Harness lock-in.* → The plugin is a thin client over MCP; the
  protocol is host-agnostic.

## Round 5 — honesty gaps

- *Silent assumptions in auto-clarify.* → Unanswered questions land in
  `open_ambiguities`; assumptions are marked, never silent.
- *Minority report could be dropped.* → Programmatic: built from the
  dissent ledger, not the chairman's prose.

## Round 6 — the ledger

- *Consensus flaps on judge noise.* → Stability rule: two consecutive checks.
- *Objection floods.* → Caps: `max_new_per_round`, `max_active`; overflow is
  `deferred`, never dropped.

## Round 7 — revision order

- *Switches before revision reward defection.* → Revision happens BEFORE
  switches; a faction may fix its platform first.
- *Revisions never close objections.* → `superseded`: author-withdraw or
  both judges agree the revision addressed it.

## Round 8 — session leaks

- *State leaks between questions.* → DebateEngine, clusterer, and consensus
  checker are created fresh per deliberation; e2e tests prove no leak.

## Round 9 — the values land

- *Router had a silent fallback classifier.* → Two classifiers from config,
  no fallback; disagreement routes to debate.
- *Judges could silently sit in the council.* → The engine refuses to start
  without two judges outside the council.
- *The three values were prose.* → Code: decision tree, plan contract,
  metric. CLI closes the elicitation loop (questions are actually asked).
- *Red_team on unanimity attacked nothing.* → The devil's advocate attacks
  the only platform directly.

## Round 10 — the values get audited

- *`is_sourced` laundered hallucinations.* → THREE labels: `sourced` (user
  or verified), `unverified_claim` (URL from memory), `assumption`. A
  hallucinated link never gets institutional weight.
- *Plan contract rendered on non-zhoda verdicts.* → Rendered ONLY on zhoda;
  a spec built on "we did not decide" is not a spec.
- *Minority ≠ rejected at split.* → `paths_rejected` counts only what a
  REACHED consensus rejected.
- *Appeal overwrote the decision silently.* → `decision_origin =
  "appeal_without_consensus"` — labeled fiat.
- *`dead_ends_prevented` was a counterfactual.* → Renamed to the honest
  `paths_rejected`; the ROI metric waits for executor feedback.

## Round 11 — metric semantics

- *The winner's accepted weaknesses vanished from the count.* →
  `paths_rejected` also collects objections that stayed open against the
  winning platform (the unaddressed version of the chosen path is what got
  rejected) — still gated on zhoda.
- *Empty count read as "the protocol found nothing".* → CLI: clean unanimity
  says "nothing was disputed, nothing to reject" — the metric stays honest,
  the presentation stops underselling it.

## Round 12 — Stage 0, synthetic opposition, cost

- *Unasked elicitation dumped into assumptions.* → Always collect/dedup;
  unasked items (auto-clarify, below threshold, leftover after top-3) land in
  `open_ambiguities`. Grounding still sees them.
- *Spawned opposition looked like a real minority.* → `Faction.synthetic`;
  minority/tree/CLI carry
  `[synthetic opposition — no council model held this position]`.
- *Rotating DA sat beside the spawned faction.* → Skip rotating DA while a
  synthetic opposition already exists.
- *`SOURCE:` was prompt-only.* → Parsed into `rebuttal_evidence_url`,
  stripped from prose; string `"null"` URLs collapse to `None`.
- *Cost snapshot froze before synthesize/plan; `latency_s` stayed 0.* →
  Snapshot after all LLM + `mark("render")`; `sum(breakdown) == requests`;
  `latency_s` from `begin_question`.
