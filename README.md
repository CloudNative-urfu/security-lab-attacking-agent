# Харнес атакующего агента (АГ) · Полигон

Production-ready **ядро харнеса** атакующего агента полигона — управляемая оболочка для
ведения **авторизованного** исследования защит стороннего сервиса (СС) строго в рамках
**КИК** (конфигурации исследовательской кампании): бюджетов, политик (deny-by-default),
областей (scope) и восстановимого состояния.

> Безопасность системы определяется **харнесом**, а не моделью. Поэтому ядро инвариантов
> (допуск, учёт расходов, изоляция, атомарность памяти, lineage чекпоинтов) реализовано
> первым и полностью покрыто тестами — до подключения тяжёлой инфраструктуры.

Это инструмент для **авторизованного тестирования безопасности** (пентест-полигон,
исследование guardrails). Он не содержит эксплойтов: «атакующие» действия — это абстрактные
вызовы инструментов к целевому сервису в пределах утверждённой КИК.

## Архитектура

Hexagonal (ports-and-adapters): чистый домен без I/O + подключаемые адаптеры инфраструктуры.

```
attacking_agent/
├── domain/                 # чистая доменная логика и порты (без I/O)
│   ├── common/             #   Money (Decimal), Scope, IdFactory, Clock, ошибки
│   ├── config/             #   campaign-config: КИК, freeze, SHA-256, публичная проекция
│   ├── policy/             #   policy-enforcement: deny-by-default, allowlist, анти-SSRF
│   ├── budget/             #   budget-ledger: reserve/start/settle, COST_UNKNOWN, BOUND_BROKEN
│   ├── tools/              #   tool-dispatch: диспетчер, журнал, изоляция runner
│   ├── llm/                #   llm-proxy: допуск → вызов → usage, без скрытых retry/fallback
│   ├── memory/             #   semantic-memory: Reader/Writer/Controller, 4 типа сущностей
│   ├── orchestration/      #   campaign-orchestration: цикл эпизода, контроллер кампании
│   └── checkpoint/         #   state-checkpoint: снимок АГ+СС, resume, fork с lineage
├── adapters/               # реализации портов
│   ├── sql/                #   SQLAlchemy источник истины (SQLite/PostgreSQL)
│   ├── http_tool.py        #   безопасный httpx-инструмент (подзапросы учтены, без авто-redirect)
│   └── litellm_client.py   #   LLMClient поверх LiteLLM (retries/fallback OFF)
├── app/                    # operator-api: FastAPI (команды + read-модели, авторизация)
└── demo.py                 # сквозной офлайн-прогон на in-memory адаптерах
```

Стек соответствует плану интеграций v8 (Python · FastAPI · Temporal · LangGraph/LangChain ·
PostgreSQL/pgvector · LiteLLM). Тяжёлые внешние системы подключаются через **порты**; для MVP
поставляются рабочие in-memory/локальные адаптеры и production-путь на PostgreSQL/LiteLLM.

## Запуск

Требуется [uv](https://docs.astral.sh/uv/). В окружении без node используйте nix для ruff.

```bash
uv sync --extra api --extra pg          # окружение (домен + API + SQL-адаптеры)
uv run python -m attacking_agent.demo   # сквозное демо (офлайн, без сети)
uv run pytest -q                        # тесты (121 шт.)
uv run mypy src                         # строгая типизация
nix run nixpkgs#ruff -- check src tests # линтер (ruff из nixpkgs на NixOS)
scripts/check.sh                        # единые ворота (ruff+mypy+bandit+detect-secrets+pip-audit+pytest+openspec)
```

### Доказательства на реальной инфраструктуре

```bash
docker compose up -d postgres temporal   # PostgreSQL 16 (pgvector) + Temporal 1.25 (dev)
AG_PG_URL='postgresql+psycopg://ag:ag@localhost:5433/ag' \
AG_TEMPORAL='localhost:7233' uv run pytest tests/integration -q
```

Интеграционные тесты (авто-скип без инфраструктуры) доказывают на реальных системах:
- **кросс-процессный бюджет и branch-fence памяти** на PostgreSQL — настоящими отдельными
  ОС-процессами (multiprocessing): жёсткий лимит и атомарность публикации держатся;
- **идемпотентность под Temporal** — при retry Activity (сбой после отправки) durable-журнал
  не допускает повторной физической отправки (ровно одна).

### Живая LLM агента (Cloud.ru Evolution ML Inference)

Транспорт модели — порт `LLMClient`; адаптер `adapters/cloudru_client.py` работает поверх
OpenAI-совместимого деплоя Cloud.ru (`evolution-openai`, IAM-токен крутит библиотека). Скрытых
retry нет (`max_retries=0`). Поддержаны reasoning-модели (при пустом `content` JSON берётся из
`reasoning_content`; `max_tokens` настраивается, дефолт 2048). Вывод валидируется по схеме
`StepDecision`; системный prompt задаёт честную рамку авторизованного оборонительного
исследования и отделяет недоверенные наблюдения от инструкций.

```bash
cp example.env .env         # вписать EVOLUTION_KEY_ID / EVOLUTION_SECRET (секреты — только в .env)
uv run python scripts/smoke_cloudru.py   # один реальный запрос: finish_reason, сырой ответ, StepDecision
```

Секреты читаются только из окружения и в код/вывод не попадают. `.env` — в `.gitignore`.

### Ворота качества и политика (AI-driven PDLC)

Единые автоматические ворота (`scripts/check.sh`, CI `.forgejo/workflows/ci.yaml`,
pre-commit): ruff с правилами безопасности (`S`/flake8-bandit), mypy `--strict`, bandit,
detect-secrets (по `.secrets.baseline`), pip-audit, pytest, `openspec validate`. Политика
реализации, обязательные инварианты и критик-ревью описаны в `ENGINEERING_POLICY.md`.

Операторский API (FastAPI) собирается фабрикой `attacking_agent.app.create_app(command_service,
read_models, auth)` — она принимает зависимости (сервисы и провайдер авторизации), поэтому
запускается из тонкого модуля-композиции, который их конструирует. Пример сборки и проверки
контракта — в `tests/app/test_operator_api.py`.

## Ключевые инварианты (все покрыты тестами)

- **КИК неизменяема** после freeze; адресуется `config_version` + SHA-256; секреты — только refs;
  публичная проекция для атакующего без приватных данных.
- **Deny-by-default**: операции и адреса вне allowlist запрещены; адрес проверяется и после
  DNS/redirect (защита от SSRF: loopback/link-local/metadata блокируются).
- **Двухфазный бюджет**: reserve → start (permit на одну попытку) → settle; Decimal-деньги;
  жёсткий лимит не превышается; `COST_UNKNOWN` удерживается и не освобождается по TTL;
  `BOUND_BROKEN` останавливает выдачу.
- **Диспетчер инструментов**: исполнение только через диспетчер (обёртка не обходит проверки);
  durable intent до отправки; результат фиксируется всегда; однократный permit; `UNKNOWN`
  блокирует зависимые действия; изоляция runner с cap подзапросов и без доверенных секретов.
- **LLM Proxy**: вызов только после допуска, usage учтён в ledger; без скрытых retry/fallback —
  повтор это новая учтённая попытка; `INVALID_OUTPUT` при несоответствии схеме.
- **Память**: один Write-путь (атомарная публикация под branch-fence, candidate hash); два
  режима чтения (TASK/ANALYSIS) с обязательным фильтром scope; Events append-only; pgvector —
  производный поиск, никогда не выдаёт чужую ветку.
- **Оркестрация**: один активный эпизод на ветку; паспорт шага фиксируется до действия;
  предложение вне КИК не выполняется; replay не вызывает повторную отправку; закрытие эпизода
  идемпотентно; исход и stop_reason раздельны.
- **Чекпоинт**: барьер/drain (UNKNOWN блокирует READY); content-addressed части + READY-head
  по command_id; `BLOCKED` без адаптера снимка СС; форк не видит будущее родителя и не
  откатывает расход.

## Топология сервисов (change `decompose-services`)

Монорепо с общей доменной библиотекой `attacking_agent`; stateless-компоненты вынесены в
сетевые сервисы **за теми же портами** (домен не меняется):

```
┌───────────────┐    команды/чтение    ┌──────────────┐
│  operator-api │◀────────────────────▶│   оператор   │
└──────┬────────┘                      └──────────────┘
       │ (общий домен)
┌──────▼────────┐  HttpLLMClient  ┌──────────────┐   provider
│  worker /     │────────────────▶│ llm-gateway  │──────────▶ модель
│  episode loop │  HttpToolRunner ├──────────────┤
│  (Temporal)   │────────────────▶│ tool-runner  │──(subprocess, allowlist)─▶ СС
└──────┬────────┘                 └──────────────┘
       │ источник истины
┌──────▼───────────────────────────┐
│ PostgreSQL (budget/journal/memory)│  + Temporal + object store
└───────────────────────────────────┘
```

- **llm-gateway** (`services/llm_gateway`, `POST /v1/complete`): чистый транспорт к модели;
  бюджет/журнал остаются у вызывающего (единый источник истины в PG).
- **tool-runner** (`services/tool_runner`, `POST /v1/run`): изолированное исполнение
  инструментов по **allowlist** (анти-RCE), субпроцесс с таймаутом/cap.
- Домен обращается к ним через `HttpLLMClient` / `HttpToolRunner` — те же порты `LLMClient`/
  `ToolRunner`, что и локально (контрактные тесты доказывают эквивалентность in-process).
- Запуск: `docker compose up -d --build llm-gateway tool-runner` (или `python -m services.<name>`).
- Источник истины (PostgreSQL) намеренно НЕ дробится — консистентность инвариантов важнее.

## Харденинг (change `harden-core`)

После независимого критик-ревью закрыты разрывы «заявлено vs реализовано»:

- **Кросс-процессный бюджет и branch-fence памяти**: реальные БД-блокировки
  (`BEGIN IMMEDIATE` на SQLite, advisory на PostgreSQL) вместо process-lock — жёсткий лимит
  и атомарность публикации держатся между воркерами (состязательные тесты).
- **Изоляция инструментов**: subprocess с **allowlist** окружения (секрет в нестандартно
  названной переменной не утекает), таймаут, cap вывода; брокерский HTTP с per-request
  политикой и неотключаемым счётчиком. Честная граница: сетевой периметр (egress-namespace/
  gVisor) — отдельный gate, subprocess даёт изоляцию памяти/секретов, не сети.
- **Атомарная идемпотентность** диспетчера (claim слота) и **durable журналы** бюджета,
  вызовов и попыток LLM (SQL-адаптеры) — повтор/гонка не вызывают вторую отправку даже
  после рестарта процесса.
- **Анти-prompt-injection**: содержимое СС идёт в контекст модели как размеченные
  недоверенные данные; главная гарантия — харнес сам проверяет область действия, поэтому
  инъекция не может расширить полномочия, даже если модель на неё поддаётся.
- **Авторизация**: подписанные токены с `exp` и `scope` (per-endpoint области).
- **Память**: ранжирующий поиск (перекрытие токенов + порт эмбеддингов) вместо подстроки;
  реальная оценка кандидата (согласованность состава/базы, критерии), не штамп.

## Границы MVP (следующие gate по v8)

Это первый вертикальный проход (gate G0–G5 + API G8 на локальных адаптерах). Намеренно вне
scope и реализовано как **порты** для последующих gate:

- **Temporal** (G5): реализован production-адаптер `TemporalExecutionEngine`
  (`adapters/temporal_engine.py`) — эпизод исполняется как Temporal Workflow → Activity;
  протестирован на мок-runner (happy path + устойчивый retry), а инвариант идемпотентности
  доказан под реальным Temporal. Это **крупнозернистая** первая версия (эпизод = одна
  Activity); тонкая разбивка на пошаговые Activity (граф эпизода как Child Workflow по v8) —
  следующий шаг. LangGraph-граф эпизода пока не вынесен в Workflow.
- **Сетевой периметр изоляции (gVisor/containerd/netns)** (G2): есть subprocess-изоляция
  памяти/секретов и брокерский HTTP, но произвольный недоверенный код может открыть сокет
  мимо брокера — сетевой egress на уровне ОС реализуется за тем же портом `ToolRunner`.
- **pgvector-поиск и снимок произвольного СС** (G3/G7): порты `MemoryStore` /
  `TargetSnapshotAdapter`; полный чекпоинт при отсутствии адаптера СС честно возвращает `BLOCKED`.
- **Точные версии моделей/пакетов, vLLM/approved API, React-UI**: фиксируются на
  compatibility-gate; в коде — абстракции семейств, без хардкода версий.

Подробности — в `openspec/` (ТЗ: proposal, design, 9 spec-дельт, tasks).

## Разработка

Spec-driven через [OpenSpec](https://github.com/Fission-AI/OpenSpec). Инварианты описаны как
контракты поведения в `openspec/specs/`; реализация ведётся по `openspec/changes/`.
Подход — TDD: тест фиксирует инвариант, затем реализация. Коммиты атомарны по capability.
