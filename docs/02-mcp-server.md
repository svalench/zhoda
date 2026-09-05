# Слой 2: zhoda-mcp — MCP-сервер

> Тонкая обёртка над zhoda-core по Model Context Protocol.
> Один сервер — работает в DeepSeek Harness, Claude Code, Codex и любой
> MCP-совместимой обвязке. Главный канал распространения проекта.

## 1. Зачем MCP, а не только плагин dsh

- MCP — кросс-harness стандарт: один сервер подключается везде, мы не привязаны
  к судьбе одного рантайма и его preview-нестабильности
- Порог входа для пользователя минимальный: одна строчка в конфиге
- Для публичной истории «автор популярного MCP-сервера» шире,
  чем «автор плагина к одному harness»

## 2. Экспортируемые инструменты

| Инструмент | Вход | Выход | Назначение |
|---|---|---|---|
| `zhoda_clarify` | `question: str` | `questions` + `estimate` | Стадия 0 отдельно: агент-хост сам задаёт вопросы пользователю |
| `zhoda_deliberate` | `question`, `confirm`, `value_map?`, `rounds_cap?`, `protocol?` | estimate **или** `Verdict` | `confirm=false` — оценка; `confirm=true` — полный цикл |
| `zhoda_verdict` | `transcript_id: str` | `Verdict` или `error` | Последний event `stage=verdict`. Нет такого event (в т.ч. start+error) — не успешный вердикт (`error`, не `status: verdict`) |
| `zhoda_transcript` | `transcript_id: str`, `format: "md" \| "json"` | хроніка | JSON: `events[0].stage` = `start`; дальше `route` … `verdict`; промежуточные стадии допустимы |
| `zhoda_reputation` | `domain?: str` | рейтинг моделей | Какой модели доверять в домене |

Принцип: инструменты возвращают структурированный JSON, а не простыню текста —
хост-агент сам решает, как показать результат пользователю.

## 3. Сценарии использования хостом

**Сценарий А — «второе мнение совета» (основной):**
Агент в dsh/Claude Code дошёл до архитектурного решения → вызывает
`zhoda_deliberate` с `confirm=false` (оценка) → после согласия пользователя
`confirm=true` → получает вердикт + minority report → показывает
развилку, если `zhoda_reached: false`.

**Сценарий Б — «сначала спроси»:**
Пользователь дал расплывчатую задачу → агент вызывает `zhoda_clarify` →
задаёт пользователю 2–4 вопроса → и только потом работает.

**Сценарий В — «проверь себя»:**
Агент написал код/план → `zhoda_deliberate` в режиме red-team →
фракции ищут дыры → агент исправляет до показа пользователю.

## 4. Транспорт и установка

- Транспорты: `stdio` (дефолт, локально) и `SSE` (для удалённого сервера)
- Дистрибуция: `pip install zhoda-mcp` + `uvx zhoda-mcp` для zero-install
- Конфиг через env: `OPENROUTER_API_KEY` (BYOK), `ZHODA_COUNCIL` (путь к YAML),
  `ZHODA_BUDGET_USD`
- Перед подключением: `cd mcp && uv sync`; `core/zhoda.yaml` (судьи вне совета);
  ключ в корневом `.env`, не в git
- `zhoda_deliberate` сначала с `confirm=false`. Дебаты — минуты, не 60 с;
  таймаут хоста поднимать (в dsh: `toolCallTimeoutMs: 600000`)

`uvx zhoda-mcp` — после публикации на PyPI.

### 4.1 Cursor

Файл проекта: `.cursor/mcp.json` (уже в репозитории). Глобально:
`~/.cursor/mcp.json`. Совпадение имени — побеждает проект.

```json
{
  "mcpServers": {
    "zhoda": {
      "type": "stdio",
      "command": "uv",
      "args": ["--directory", "${workspaceFolder}/mcp", "run", "zhoda-mcp"],
      "envFile": "${workspaceFolder}/.env",
      "env": {
        "ZHODA_COUNCIL": "${workspaceFolder}/core/zhoda.yaml",
        "ZHODA_BUDGET_USD": "10"
      }
    }
  }
}
```

GUI Cursor часто не видит PATH из zsh — тогда `command` = абсолютный путь
к `uv` (`/opt/homebrew/bin/uv`). Перезапуск или тумблер в Customize → MCP.
Логи: Output → MCP Logs. Имена тулов без префикса: `zhoda_deliberate`.

Документация Cursor: https://cursor.com/docs/mcp

### 4.2 DeepSeek Harness

Нативный dsh — **не** JSON `mcpServers`. Один инстанс плагина
`@deepseek-ai/dsh-mcp-client` = один MCP-сервер, монтируется патчем
`$DSH_HOME/profiles/web/cordis.patch.yml` (обычно `~/.dsh/...`).
Если файл — `[]`, заменить целиком. Ключ — `!!js process.env.OPENROUTER_API_KEY`,
не plaintext. Пример: `mcp/examples/dsh.cordis.patch.yml`.

```yaml
- insert:
    - id: mcp-zhoda
      name: "@deepseek-ai/dsh-mcp-client"
      config:
        serverName: zhoda
        transport: stdio
        command: uv
        args:
          - --directory
          - /ABS/PATH/zhoda/mcp
          - run
          - zhoda-mcp
        env:
          OPENROUTER_API_KEY: !!js process.env.OPENROUTER_API_KEY
          ZHODA_COUNCIL: /ABS/PATH/zhoda/core/zhoda.yaml
          ZHODA_BUDGET_USD: "10"
        toolCallTimeoutMs: 600000
        failOnStartupError: true
```

Тулы: `mcp__zhoda__zhoda_clarify`, `mcp__zhoda__zhoda_deliberate`, …
Проверка: `dsh web --dump-config | grep -A4 mcp-zhoda`.
`mcp/examples/dsh.json` — только для чужих хостов с формой `mcpServers`.

Плагин: https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/mcp/mcp-client/README.md
Патч `insert`: https://github.com/deepseek-ai/deepseek-harness/blob/master/examples/mcp-memory/README.md

### 4.3 Claude Code / Codex

Общий фрагмент `mcpServers` — `mcp/examples/claude-code.json`,
`mcp/examples/codex.json`.

## 5. Архитектура слоя

```
хост (dsh / Claude Code / Codex)
   │  MCP (stdio/SSE)
   ▼
zhoda-mcp
   ├─ валидация входов (Pydantic)
   ├─ rate limiting и бюджетный стоп-кран
   ├─ ──HTTP──► zhoda-core (если развёрнут отдельно)
   └─ ──in-proc──► zhoda-core (если embedded, дефолт)
```

- Дефолт: ядро встроено в процесс MCP-сервера (zero-ops для пользователя)
- Опция `ZHODA_CORE_URL` — внешнее ядро (команда/сервер с общей репутацией)

## 6. Ограничения и честность

- Дельберация — это 15–40 запросов к моделям: инструмент обязан сообщать
  хосту оценку стоимости/времени ДО запуска (поле `estimate` в `zhoda_clarify`
  и в `zhoda_deliberate` при `confirm=false`). Хост подтверждает явным
  `confirm=true`, иначе совет не стартует.
- Таймауты хостов: стримим прогресс через MCP notifications, чтобы хост
  не убивал долгий вызов
- Бесплатные модели OpenRouter имеют дневные квоты — при исчерпании возвращаем
  честную ошибку `quota_exceeded` с инструкцией, а не молчаливую деградацию

## 7. Публикация

- [ ] PyPI: `zhoda-mcp`
- [ ] Реестры MCP: официальный servers-репозиторий, mcp.so, Smithery
- [ ] README с гифкой: «Claude Code спрашивает совет у фракций моделей»
- [ ] Кросс-пост: Dev.to + Medium + r/LocalLLaMA

## 8. Задачи этапа

- [x] Скелет на `mcp` Python SDK (FastMCP), stdio-транспорт
- [x] Инструменты clarify/deliberate/verdict/transcript/reputation поверх ядра
- [x] Оценка стоимости и MCP-нотификации прогресса
- [ ] SSE-транспорт + `ZHODA_CORE_URL` (SSE: `ZHODA_MCP_TRANSPORT=sse`;
      удалённое ядро честно отвечает `remote_core_unwired`)
- [x] Конфиги-примеры для Cursor, dsh (cordis patch), Claude Code, Codex
- [ ] Публикация в PyPI и реестрах
