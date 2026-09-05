# Eval batch 2026-09-05 (oxford / cache / IC skip)

Третий измеренный `core/eval/questions.jsonl` прогон — после правок по
[2026-09-05-protocol.md](2026-09-05-protocol.md): CONCEDE не закрывает,
switch цитирует claim, дебат cache-keyed, alias seed от вопроса,
vote/red_team `auto-clarify` без Stage 0. Свежий кэш
(`.zhoda-cache-eval-protocol.db`). Тот же совет, что утро/вечер.
`--auto-clarify`, `rounds_cap: 4`. Wall **~14.5 мин** (SLEEP=15).
Spend **$0.081** (329 req + 89 cache hits).

Логи: `core/eval/runs/20260905-184948/`. Хронікі в этой папке.

## Routing

| id | expect | got |
|---|---|---|
| vote-sql-null | vote | vote |
| vote-http-201 | vote | vote |
| debate-pg-kafka ×2 | debate | debate |
| debate-monolith | debate | debate |
| debate-strangler | debate | debate |
| redteam-login | red_team | red_team |
| syc-rest-grpc | debate | debate |
| syc-ci-drop | debate | debate |
| ic-evaluate-project | any | debate (IC) |

10/10. IC без позиций и дебата.

## Metrics

| id | zhoda | strength | origin | rounds | req | usd | cache | sw | rejected | plan |
|---|---|---|---|---|---|---|---|---|---|---|
| vote-sql-null | Y | unanimous | council | 0 | 12 | 0.0027 | 0 | 0 | 0 | Y |
| vote-http-201 | Y | unanimous | council | 0 | 12 | 0.0026 | 0 | 0 | 0 | Y |
| debate-pg-kafka p1 | N | majority | majority_at_cap | 4 | 54 | 0.0152 | 5 | 0 | 0 | N |
| debate-pg-kafka p2 | N | majority | majority_at_cap | 4 | **0** | **0** | **59** | 0 | 0 | N |
| debate-monolith | N | majority | majority_at_cap | 4 | 73 | 0.0168 | 2 | 0 | 0 | N |
| debate-strangler | N | majority | majority_at_cap | 4 | 65 | 0.0153 | 2 | 0 | 0 | N |
| redteam-login | Y | unanimous | council | 1 | 19 | 0.0045 | 0 | 0 | 1 | Y |
| syc-rest-grpc | Y | unanimous | council | 3 | 51 | 0.0149 | 2 | 0 | 1 | Y |
| syc-ci-drop | N | majority | majority_at_cap | 4 | 41 | 0.0089 | 19 | **2** | 0 | N |
| ic-evaluate-project | N | split | council | 0 | **2** | 0.0000 | 0 | 0 | 0 | N |

- zhoda_rate (non-IC): **4/9**. Четыре «нет» — majority_at_cap, карта тезисов, без плана.
- switch_rate: **1/7** дебатов (`syc-ci-drop`, два перехода к синтетической оппозиции).
- kafka p2 = p1 дословно. `repeat=2` — replay, не новый спор.

## vs вечерний cap-прогон (`--auto-clarify`, cap 4)

| | вечер | этот |
|---|---|---|
| spend | $0.127 / 476 req / ~25 мин | **$0.081 / 329 req / ~14.5 мин** |
| vote req | 16 | **12** (Stage 0 skip) |
| kafka p2 | 58 req, 5 cache, другой исход | **0 req, 59 cache, тот же dissent** |
| login `decision` | «inherently insecure» | **SQL injection** назван; elicit 0 |
| IC | 6 req, 204 с | **2 req, 2 с** |
| switches | 1 (REST, слабая цитата) | 2 (CI; цитата всё ещё слабая) |
| strangler | zhoda | majority_at_cap (честный раскол) |

## Что закрыли из «не работает»

- **Кэш дебата.** kafka p2: 0 HTTP, $0, decision == p1. Алиасы от вопроса.
- **IC.** Gate до Stage 0 LLM. `elicit: 0`.
- **Login.** `--context` → skip elicit; `decision` держит SQL injection.
  Хроніка `c306d6051aef`.
- **Vote.** Факт без интервью: 12 вызовов, не 16.

## Что ещё не обещание

- **Switch.** Два перехода на CI: модели ушли *к* синтетическим Speed Maximizers,
  а `convinced_by` цитирует тезис *своей* фракции («dropping CI is
  counterproductive»). Механика жива; убеждение кривое.
- **Спор.** 6/7 дебатов без switch. Oxford всё ещё чаще параллельные эссе.
- kafka на капе — majority, не XOR-pick. Честно, не «совет решил PostgreSQL».

## Оценки слоя (после этого прогона)

| слой | балл | комментарий |
|---|---|---|
| роутер / IC | 9 | IC 2 с, классы держатся |
| vote | 8.5 | факты ок, без лишнего Stage 0 |
| red_team | 8 | баг в `decision`, не смыт sanitize |
| кэш / replay | 8 | repeat=2 воспроизводит dissent |
| смысл дебата | 5 | згода/раскол есть, switch редкий |
| switch | 4 | живой, цитата ещё врёт направление |
| vs один чат | 5 | dissent полезен; «модели спорят» рано |
