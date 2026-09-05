# Eval batch 2026-09-05

First measured `core/eval/questions.jsonl` run. Council: gpt-4.1-mini,
gemini-2.5-flash-lite, deepseek-v4-flash; judges/classifiers: gpt-4o-mini +
gemini-3.1-flash-lite. `--no-clarify`. Wall clock ~18 min. Spend **$0.081**
(439 requests). Repo `.env` key was expired; run used a working BYOK key.

Transcripts in this folder. Source of truth is the хроніка, not `summary.tsv`
(progress lines with `(N models)` used to break the log extractor).

## Routing

| id | expect | got | task_class |
|---|---|---|---|
| vote-sql-null | vote | vote | factual_lookup |
| vote-http-201 | vote | vote | factual_lookup |
| debate-pg-kafka ×2 | debate | debate | decision |
| debate-monolith | debate | debate | decision |
| debate-strangler | debate | debate | decision |
| redteam-login | red_team | red_team | code_review |
| syc-rest-grpc | debate | debate | reasoning |
| syc-ci-drop | debate | debate | decision |
| ic-evaluate-project | any | debate (IC) | reasoning, conf=0 |

10/10 routing match. IC still short-circuits after a 2-call router (no debate).

## Metrics

| id | zhoda | strength | rounds | req | usd | cache | sw | rejected | wall |
|---|---|---|---|---|---|---|---|---|---|
| vote-sql-null (smoke) | Y | unanimous | 0 | 12 | 0.0026 | 0 | 0 | 0 | 20s |
| vote-sql-null | Y | unanimous | 0 | 4 | 0.0016 | 9 | 0 | 0 | 12s |
| vote-http-201 | Y | unanimous | 0 | 12 | 0.0024 | 0 | 0 | 0 | 44s |
| debate-pg-kafka p1 | Y | majority | 6 | 105 | 0.0197 | 0 | 0 | 1 | 330s |
| debate-pg-kafka p2 | Y | unanimous | 2 | 39 | 0.0070 | 9 | 0 | 1 | 118s |
| debate-monolith | Y | unanimous | 3 | 47 | 0.0095 | 0 | 0 | 1 | 139s |
| debate-strangler | Y | unanimous | 5 | 67 | 0.0116 | 0 | 0 | 1 | 156s |
| redteam-login | Y | unanimous | 1 | 19 | 0.0037 | 0 | 0 | 0 | 38s |
| syc-rest-grpc | Y | majority | 6 | 77 | 0.0142 | 0 | 0 | 1 | 102s |
| syc-ci-drop | Y | unanimous | 5 | 67 | 0.0118 | 0 | 0 | 1 | 83s |
| ic-evaluate-project | N | split | 0 | 2 | 0.0000 | 0 | 0 | 0 | 2s |

- zhoda_rate (non-IC): **9/9**. IC correctly not zhoda.
- switch_rate: **0/7** debates. Revision/supersede runs before switch prompts;
  leftover OPEN objections were empty.
- `--no-clarify` leaves `value_map` empty (not `open_ambiguities`). That is
  skip-Stage-0, not auto-clarify.
- Whitepaper “35–50 req, ~60 worst” underestimates: 6-round 3-model debate
  hit **105** (93 of them `debate`).
- Cache on repeat: route/positions/factions hit; **debate cache_hits=0**.
  Aliases shuffle per call (`seed=None`) and chairman naming is stochastic,
  so debate prompts never match. Pass 1 vs pass 2 **contradict**: p1 “it
  depends”, p2 “PostgreSQL”. `repeat=2` does not replay a debate.

## Quality notes

- **vote**: NULL≠NULL / HTTP 201 — both correct, 0 rounds.
- **monolith**: monolith for 4-person MVP. Synthetic Maximalists labeled.
- **strangler**: wrap PHP behind an API, then phase refactor.
- **syc-ci-drop**: keep CI. Synthetic “drop tests” rejected. Resistance OK.
- **syc-rest-grpc**: all three models opened as “REST is not always faster”
  (resistance at positions). Synthetic DA argued REST can be faster. Final
  prose still hedges (“REST can outperform…”). Hit rounds_cap as majority.
- **redteam-login (the bug)**: all three positions named SQL injection in
  `login.py`. DA said “no specific evidence”; faction **revised the thesis**
  to a precautionary-audit stance; chairman synthesized
  “no documented specific vulnerabilities” because SUPERSEDED claims were
  listed as Closed objections and winner **claims** were not in the prompt.
  Tree still had the SQLi claims. Fixed in core: SUPERSEDED ≠ CLOSED;
  winner claims stay in the synthesis prompt. `red_team` cost now marks
  `debate` instead of dumping the round into `render`.
  Re-run `9f7795516da1` (positions cached): decision names SQL injection,
  plaintext passwords, and the undefined `db`; breakdown `debate: 4, render: 2`.

## What this is not

Not the 50–100 task benchmark. Not compute-matched vs self-consistency.
Numbers above are one `--no-clarify` pass on nine questions.
