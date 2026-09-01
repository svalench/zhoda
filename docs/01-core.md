# Zhoda Core

The Python engine. Mirrors `core/src/zhoda_core/` — schema changes land in
models.py and this file in the same commit (Cursor rule 10-python-core).

## Principles

1. **Understand before solving** — no answer before the goal is explicit.
2. **Argue in factions** — deliberation between groups, not isolated reviewers.
3. **Zhoda or honest dissent** — consensus or a structured disagreement map.
4. **Auditability** — every verdict is reproducible from its transcript.
   The хроніка opens with a `start` event at create (never an empty file).
   A provider crash appends `error` and re-raises; the id is still printed.
5. **Cost honesty** — free models first, explicit budget caps, no hidden spend.

## Protocols

| Protocol | When | Rounds |
|---|---|---|
| `vote` | factual_lookup, creative | 0 |
| `debate` | decision, reasoning | up to `rounds_cap` (default 4) |
| `red_team` | code_review | 1 |

## Router

Two cheap classifiers **from config** (`router_classifiers`), never a silent
fallback (round-9 §1). Disagreement or confidence < 0.6 → `debate`. A forced
protocol reports confidence 1.0.

## Stage 0 — Elicitation

Each council model returns ambiguities; an aggregator keeps the questions
with the highest decision impact. Duplicate questions from different models
are merged (exact match, then a cheap model groups paraphrases). Each turn
the user sees at most three questions. After answers the council is asked
again: if high-impact ambiguities remain, another batch is asked, until
models return none, the user skips the batch, or `max_elicit_turns`
(default 4). Modes: `smart` (ask the user), `no-clarify`,
`auto-clarify` (no prompts; unasked items land in `open_ambiguities`,
never in `assumptions`). Unanswered questions land in
`value_map.open_ambiguities` — honestly, never silently assumed (round-5 §2,
round-12). Leftover questions unasked when the loop stops also stay in
`open_ambiguities`. Grounding questions are asked first in a batch.
Answers are validated against options: a digit `1..n` maps to that option;
a free-text reply that contains **exactly one** option as a substring maps
to that option; pasting the option list or a non-matching string is treated
as unanswered, never as a constraint. Empty answers always stay in
`open_ambiguities`.

`--context path` (repeatable) injects file contents into elicitation and
position prompts as `Context:`. A caller may pass a pre-built `value_map`
(MCP after `zhoda_clarify`) — Stage 0 LLM calls are skipped; grounding
still runs. If the question refers to an external
object (project, repo, document) and Stage 0 still cannot name that object
— the user answered a URL, “this project”, or nothing, and no `--context`
was given — the engine returns `insufficient_context=True`,
`consensus_strength=SPLIT`, `zhoda_reached=False`, decision
`INSUFFICIENT_CONTEXT: …`, and **does not** collect positions or run
debate.

## Stage 1 — Positions

```python
Claim { claim, evidence_url?, confidence, verified }
# label: "sourced" (verified or user-provided) |
#        "unverified_claim" (URL named from memory) | "assumption" (no URL)
# round-10 §1: a hallucinated link never gets institutional weight
Position { model, thesis, answer, claims[], falsifiability, confidence }
```

Positions are anonymized from the start (`model` holds the alias).
A shared **user context** block (`ValueMap.as_prompt_block`: goal, success
criteria, constraints including answered Q&A, anti-goals, unresolved
ambiguities) is prepended to every prompt that reasons about the question
(positions, faction synthesis, critiques, rebuttals, revision, consensus,
verdict, appeal). The cache key hashes the full prompt, so a new answer
cannot replay a stale position.

## Stage 2 — Factions

Exact-normalized-thesis prefilter (free, logged as `prefilter_merges`) with a
negation guard. Then **one** non-conflicted judge decides `same` per pair on
the **primary recommendation** (thesis + answer): the same stack/system as
the main choice. Managed vs self-hosted, caveats, and optional complements
do not split a pair. The judge **pair** is reserved for consensus and
closure — pairwise AND of two judges would under-merge. Disagreements go to
the divergence ledger. The chairman names factions — sanitized, unique names.
A spawned opposition faction carries `synthetic=True` (reserved member, not a
council vote). Naming does not clear that flag.

## Stage 3 — Debate rounds

Order per round: critiques → devil's advocate → rebuttals → closures →
**revision** → switches (round-7 §2: a faction may fix its platform BEFORE
anyone is asked to defect).

- **Objection ledger**: `open | closed | superseded`. Caps:
  `max_new_per_round` (3) and `max_active` (6); overflow is marked
  `deferred`, never dropped (round-6 §2).
- **Devil's advocate**: attacks the leading faction; on unanimity
  (red_team) attacks the only platform directly (round-9 §4). On `debate`,
  if clustering produced a single faction, the advocate **spawns an
  opposition faction** with a different primary action (reserved member,
  `synthetic=True`, not a council vote). The rotating devil's advocate is
  **skipped** while that synthetic faction already sits at the table — one
  chair, not two. If spawn is off or fails, birth unanimity
  **fast-passes** (`rounds_taken = 0`, transcript `fast_pass:
  unanimity_at_birth`) instead of empty stability rounds.
- **Rebuttal `SOURCE:`** lines are parsed into `rebuttal_evidence_url` and
  stripped from prose; a URL named from memory is `unverified_claim`.
- **Closure**: both judges (outside the council, no silent fallback —
  round-9 §2) must agree the rebuttal addressed the objection.
- **Superseded**: the author withdraws, or both judges agree the revision
  addressed the objection (round-7 §1).
- **Switches**: open objection by ID + non-empty citation + the target IS
  the objection's author faction.

## Stage 4 — Consensus

The judge pair scores `all_agree` on the same primary-recommendation
criterion (thesis + answer), not prose similarity: **recommended actions /
architecture on the critical path**, not labels. Stability rule: two
consecutive **unanimous** checks (`all_agree`) must agree before zhoda is
declared early (round-6 §1). Headcount majority (2/3 of voting heads) does
**not** end the debate — it may count as zhoda only at `rounds_cap` if that
majority also persisted for `stability_rounds`. `split` at the rounds cap
becomes `deadlock`. Escalation is opt-in and
fires on deadlock only; the appellate decision overwrites `decision` but
carries `decision_origin = "appeal_without_consensus"` — a single model's
fiat is labeled, never mistaken for zhoda (round-10 §2).

## Stage 5 — Verdict

```python
Verdict {
  decision, zhoda_reached, consensus_strength, protocol,
  decision_origin,        # "council" | "appeal_without_consensus"
  router_confidence, value_map,
  minority_report,        # preserved dissent — never erased; synthetic
                          # opposition is labeled (round-12)
  dissent_map[], switches[], rounds_taken, cost, transcript_id,
  plan_contract?,         # rendered ONLY on zhoda (round-10 §2)
  paths_rejected[],       # honest programmatic count (rounds 10-11)
  decision_tree, escalated_to?,
  insufficient_context,  # True → no debate; object of evaluation missing
}
```

### The three values (rounds 9–11)

1. **Decision tree** — the verdict as a tree: argument → what closed it →
   who switched. Evidence labels are THREE states; a URL named from memory
   is `unverified_claim`, visually closer to an assumption than to a source.
2. **Plan contract** — a spec for a CHEAPER executor model: steps with
   goals, hard constraints, forbidden paths, acceptance criteria. Rendered
   only when zhoda was reached — a plan built on "we did not decide" would
   hand the executor a spec founded on dissent. `rejected_paths` and
   `open_ambiguities` are overwritten programmatically: the model writes
   prose, the protocol owns the facts.
3. **`paths_rejected`** — an honest count of what a REACHED consensus
   rejected: minority positions that lost the vote, plus objections that
   stayed open against the winner (accepted weaknesses — the unaddressed
   version of the chosen path is what got rejected). At split/deadlock:
   empty — an unresolved dispute is not a rejection. The counterfactual
   "dead ends prevented" ROI metric waits for executor feedback: we don't
   promise unmeasured numbers.

On `UNANIMOUS`, `minority_report` is empty — the judges already said it is
one position, even if faction objects were not merged. A synthetic
opposition in the minority is labeled
`[synthetic opposition — no council model held this position]` (round-12;
same honesty as `decision_origin = "appeal_without_consensus"`). On split/deadlock,
`decision` is a dissent map of every faction thesis, not the leading
faction's raw `answer`. On zhoda, the chairman **synthesizes** `decision`
for the user (action first, closed objections, overturn conditions);
unresolved ambiguities must not be asserted as facts. Fallback is the
winner's thesis, never the raw platform answer. If Stage 0 cannot ground
the object of evaluation, `insufficient_context` short-circuits: SPLIT,
no zhoda, no position or debate spend.

## Cost honesty

Per-question budget cap, config price table, semantic cache
(`cache_hits` counted), per-stage request breakdown in every verdict.
`sum(breakdown.values()) == requests`. A stage with 0 requests and
`cache_hits > 0` is shown as `cached`, not `0`. `latency_s` is wall-clock from
`begin_question` to the final snapshot, taken **after** all LLM calls
(including decision synthesis and plan contract).

## Session state

`DebateEngine`, `FactionClusterer`, `ConsensusChecker` are created fresh per
question (round-8 §1) — no objections, divergences, or judge streaks leak
between deliberations.

## Config (zhoda.yaml)

`council`, `judges` (≥2 outside the council — the engine refuses to start
otherwise), `router_classifiers` (two distinct), `chairman`, `rounds_cap`,
`stability_rounds`, `devils_advocate`, `ambiguity_threshold`,
`max_new_per_round`, `max_active`, `max_elicit_turns`,
`escalation.{enabled,model}`,
`budget_per_question_usd`, `max_concurrency`, `prices`, `cache_path`,
`transcripts_dir`.
