# Live G v2 — STOP (предусловия)

Started: not started
Stopped: 2026-09-06T14:30:47+00:00
Stop reason: `preconditions_failed` (`gold_expected_action_unresolved`)
Execution SHA: `84ad1fe9609eea6fea7cbb4349fc78414cc873c0` (`main`)
Spend: $0.0000 / $2.00
n complete (3 arms × 2 replicates): 0 / 36
Ungraded/failed share: 1.000
Primary Δ = action_correct(short_review) − action_correct(oxford): **n/a**
Decision rule: **inconclusive** — rule1: n_complete=0 < 30 or ungraded/failed share 1.000 > 0.20
Product default in code: **debate** (not flipped).

Independent validation: **false**. Live: **false**. freeze-manifest не вызывался.
Старый каталог `docs/live-runs/2026-09-06-g-pilot/` не тронут
(byte-identical `9abbe24`).
n_cached = 0 (прогона не было). evaluator_usd = 0.

## Предусловия

| Gate | Результат |
|---|---|
| main содержит P1 (pilot-grader.v2), P2 (rescore v2), P3 (fresh-cache guard) | **PASS** — `origin/main` = `84ad1fe`. P1/P2 в squash-land; P3 `402939e` — предок. |
| disagreement-log заполнен; merged-draft без `label_status=disputed` по `expected_action` | **FAIL** — log OPEN, `resolution`/`resolved_by` пустые. hol-min-001…006: `disputed_fields` содержит `expected_action`. |
| владелец подтвердил бюджет и запуск | **PASS** — этот запрос, кап $2.00 |

Кап, cache_mode=fresh, freeze-manifest, OpenRouter — не выполнялись.
P3 guard не вызывался (нет sqlite). Роутер не менялся.
