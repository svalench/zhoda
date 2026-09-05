# Zhoda Core

The Python engine. Mirrors `core/src/zhoda_core/` — schema changes land in
models.py and this file in the same commit (Cursor rule 10-python-core).

## Principles

1. **Understand before solving** — no answer before the goal is explicit.
2. **Argue in factions** — deliberation between groups, not isolated reviewers.
3. **Zhoda or honest dissent** — consensus or a structured disagreement map.
4. **Auditability** — every verdict is reproducible from its transcript.
   The хроніка opens with a `start` event at create (never an empty file).
   A successful run then records `route`, any intermediate stages
   (`positions`, `round`, …), and `verdict`. Extra events are allowed;
   the contract is order (`start` before `route` before `verdict`), not a
   three-event list. A provider crash appends `error` (no `verdict`) and
   re-raises; the id is still printed.
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
debate. `auto-clarify` / `no-clarify` skip Stage 0 LLM when that gate is
already decidable from the question alone (no `--context`). A **loaded
premise** in the question (`why is` / `since` / `given that` /
`everyone agrees`, or bare always/never when the question is not XOR)
is recorded in `open_ambiguities` even in `no-clarify` — never as a
constraint. Positions, debate and synthesis must challenge it. `vote` and
`red_team` also skip Stage 0 in `auto-clarify` / `no-clarify`: a factual
lookup is not an interview, and `red_team` must not invent “maybe the
driver sanitizes” ambiguities that wash findings out of `decision`.
`red_team` with `--context`: the source *is* the object.

## Stage 1 — Positions

```python
Claim { claim, evidence_url?, confidence, verified }
# label: "sourced" (verified or user-provided) |
#        "unverified_claim" (URL named from memory) | "assumption" (no URL)
# round-10 §1: a hallucinated link never gets institutional weight
Position { model, thesis, answer, claims[], falsifiability, confidence }
```

Positions are anonymized from the start (`model` holds the alias).
Aliases are shuffled per deliberation; the default seed is
`hash(question + council + context)` so a repeat can hit the cache.
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
  `synthetic=True`, not a council vote) that still must not treat a loaded
  premise as a fact. The rotating devil's advocate is
  **skipped** while that synthetic faction already sits at the table — one
  chair, not two — **and skipped when two or more council factions already
  oppose each other** (live 2026-09-05: extra DA filled the objection cap
  and never produced a switch). If spawn is off or fails, birth unanimity
  **fast-passes** (`rounds_taken = 0`, transcript `fast_pass:
  unanimity_at_birth`) instead of empty stability rounds.
- **Rebuttal `SOURCE:`** lines are parsed into `rebuttal_evidence_url` and
  stripped from prose; a URL named from memory is `unverified_claim`.
- **Closure**: both judges (outside the council, no silent fallback —
  round-9 §2) must agree the rebuttal **refutes** the specific claim.
  Acknowledgment and `CONCEDE` never close — the objection stays `OPEN`
  so revision/switch can fire (live 2026-09-05: easy closures → switch ≈ 0).
- **Superseded**: the author withdraws, or both judges agree the revision
  addressed the objection (round-7 §1).
- **Revision**: hedge that replaces a named pick is refused. On an XOR
  question (A or B / A vs B) a revision that **flips the named pick** is
  refused — that is a switch, not a platform rewrite. A revision that
  adopts a loaded premise the previous thesis already challenged is
  refused.
- **Switches**: open objection by ID + citation that **quotes the objection
  claim** (not a restatement of the destination thesis) + the target IS
  the objection's author faction. Critiques must quote the opponent thesis
  (cross-examination, not parallel essays). A switch from a thesis that
  already challenges a loaded premise toward one that adopts it is
  refused.
- **Cache**: every debate/consensus/verdict LLM call is keyed by
  `(stage, model, prompt)`. Aliases are seeded from
  `hash(question + council + context)` when `alias_seed` is unset, so a
  repeat of the same inputs can replay. A different question still
  shuffles differently.

## Stage 4 — Consensus

The judge pair scores `all_agree` on the same primary-recommendation
criterion (thesis + answer), not prose similarity: **the main pick that
answers the user question**. Complements and caveats are the same position
only if that pick matches. PostgreSQL-as-ledger vs Kafka-as-ledger do **not**
agree just because each mentions the other as optional. The agreement prompt
includes the question. Stability rule: two
consecutive **unanimous** checks (`all_agree`) must agree before zhoda is
declared **early**. Headcount majority (2/3 of voting heads) does
**not** end the debate. At `rounds_cap` the current check is terminal:
unanimous (even streak 1) is zhoda. Majority at the cap is **not** zhoda —
`decision_origin = "majority_at_cap"`, no plan contract. `decision` leads
with `Recommended (majority at cap, not zhoda):` + the leading faction
**thesis** (not raw `answer`), then a `Dissent:` list of the other theses.
If that thesis **adopts a loaded premise**, the labeled rec is a protocol
premise-reject (`The premise is false: … are not confirmed constraints`);
the minority theses stay in `Dissent`. That agreement is **not zhoda**.
Split/deadlock stay a full thesis map under `No zhoda`. `split` at the
rounds cap becomes `deadlock`. Escalation
is opt-in and fires on deadlock only; the appellate decision overwrites
`decision` but carries `decision_origin = "appeal_without_consensus"` — a
single model's fiat is labeled, never mistaken for zhoda (round-10 §2).

## Stage 5 — Verdict

```python
Verdict {
  decision, zhoda_reached, consensus_strength, protocol,
  decision_origin,        # "council" | "appeal_without_consensus" | "majority_at_cap"
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
faction's raw `answer`. On majority-at-cap it is still not zhoda, but the
leading **thesis** is the recommended action (labeled), then dissent —
unless the thesis adopts a loaded user premise, in which case the rec
is a protocol reject of that premise (not a silent copy of the minority).
Unanimous adoption of a loaded premise is demoted: not zhoda, majority-at-cap
format. On zhoda, the chairman **synthesizes** `decision`
for the user (action first, closed objections, overturn conditions);
unresolved ambiguities must not be asserted as facts. The question's
wording is not a confirmed constraint: a loaded premise must be rejected,
not explained as if it were true. `SUPERSEDED`
objections are platform revisions, not refutations — they must not be
bucketed with `CLOSED`. Winner `claims` stay in the synthesis prompt even
if the thesis was watered down. If the chairman omits those claims, they
are appended as `Findings:`. A hedge decision ("it depends", "both
comparable", "choose based on team expertise") falls back to the winner
thesis. On an XOR question (A or B / A vs B), a hybrid that adopts both
options as the action also falls back to the winner thesis. A hedge revision
that replaces a named pick is refused. XOR pick-flip revision is refused.
Fallback is
the winner's thesis, never the raw platform answer. A synthesis that
adopts a loaded premise falls back to a challenging winner thesis, or to
the protocol premise-reject. If Stage 0 cannot ground
the object of evaluation, `insufficient_context` short-circuits: SPLIT,
no zhoda, no position or debate spend.

## Cost honesty

Per-question budget cap, config price table, semantic cache
(`cache_hits` counted), per-stage request breakdown in every verdict.
`sum(breakdown.values()) == requests`. A stage with 0 requests and
`cache_hits > 0` is shown as `cached`, not `0`. Debate rounds, consensus,
naming, decision synthesis and the plan contract are cache-keyed (not
`transcript_id` — that would kill replay). `latency_s` is wall-clock from
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

## Benchmarks

`python -m zhoda_core.benchmarks` compares Zhoda debate to vote, a
single-pass council, self-consistency, and best-of-N. `--suite decision`
is 51 tasks (XOR architecture, security, ops, plus sycophancy/minority
seeds). `paths_rejected` on a reached zhoda is `dead_ends`; the report
adds `avg_dead_ends` and `dead_ends_per_usd`. Headline accuracy is
keyword-first; `--judge llm` overlays a blind committed-pick judge
(arm name hidden; dissent map is a miss). Each compare arm gets its
own sqlite (`cache-zhoda.db`, `cache-majority.db`, …) so vote does
not reuse debate completions. `--shared-cache` restores the old leak.
Live numbers: [docs/benchmarks-and-reputation.md](benchmarks-and-reputation.md)
and `docs/live-runs/`.
