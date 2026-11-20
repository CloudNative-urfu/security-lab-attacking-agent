# Tasks

## 1. Каркас проекта и инфраструктура разработки

- [x] 1.1 Инициализировать Python-проект через uv (`pyproject.toml`, пакет `attacking_agent`), зависимости families и dev-tools (pytest, ruff, mypy)
- [x] 1.2 Настроить конфигурацию инструментов (ruff, mypy, pytest, coverage) и `.gitignore`
- [x] 1.3 Завести структуру пакетов: `domain/`, `adapters/`, `app/`; заготовки портов (Protocol)
- [x] 1.4 Базовые типы идентичности и ошибок (ids: op/attempt/reservation/charge/command; Result-типы статусов)

## 2. campaign-config (КИК)

- [x] 2.1 Тесты: валидация разделов, запрет сырых секретов, детерминированный хэш, неизменность, публичная проекция
- [x] 2.2 Pydantic-модели ObjectiveSpec/TargetSpec/BudgetSpec/PolicySpec/MemoryStateSpec и агрегат КИК
- [x] 2.3 Канонизация состава + SHA-256, freeze, публичная проекция; все тесты зелёные

## 3. policy-enforcement

- [x] 3.1 Тесты: deny-by-default, allowlist операций/адресов, повторная проверка после DNS/redirect, предложение модели ≠ разрешение
- [x] 3.2 Доменная проверка ограничений + порт `AddressResolver`; локальный резолвер с блокировкой приватных диапазонов; тесты зелёные

## 4. budget-ledger

- [x] 4.1 Тесты: типы/уровни, Decimal-деньги, reserve/start/settle, идемпотентность settle, жёсткий лимит, COST_UNKNOWN+TTL, BOUND_BROKEN
- [x] 4.2 Доменный менеджер бюджетов + порт `BudgetStore`; in-memory store с lock-семантикой; тесты зелёные

## 5. tool-dispatch и журнал

- [x] 5.1 Тесты: вызов только через диспетчер, невалидные параметры, intent до отправки, result всегда записан, однократный permit, UNKNOWN блокирует, cap подзапросов, отсутствие секретов в runner
- [x] 5.2 Контракт инструмента (StructuredTool-подобный), порт `ToolRunner` и `CallJournal`; диспетчер, связывающий policy+budget+journal; in-memory адаптеры
- [x] 5.3 Локальный runner с egress-allowlist и cap подзапросов; безопасный HTTP-инструмент поверх порта; тесты зелёные

## 6. llm-proxy

- [x] 6.1 Тесты: запрет без допуска, учёт usage в ledger, отсутствие скрытых retry/fallback, INVALID_OUTPUT, исправление как отдельная попытка
- [x] 6.2 Порт `LLMClient`; доменный Proxy (допуск→вызов→usage→settle), валидация структурированного ответа; fake-клиент
- [x] 6.3 Адаптер поверх LiteLLM (retries/fallback выkey OFF), создаётся на worker-слое; тесты зелёные (адаптер — unit с заглушкой транспорта)

## 7. semantic-memory

- [x] 7.1 Тесты Reader: TASK/ANALYSIS, фильтр scope/branch, недоступность preview в TASK, точный fallback/INCOMPLETE
- [x] 7.2 Тесты Writer: append-only Events, PREPARE→EVALUATE→COMMIT, candidate hash, атомарная публикация под fence, прежняя квитанция, аннулирование approval
- [x] 7.3 Тесты Controller: применимость, план курации передаётся Writer (не пишет напрямую)
- [x] 7.4 Доменные модели сущностей + порт `MemoryStore`; реализация Reader/Writer/Controller; in-memory store с branch lock; тесты зелёные

## 8. campaign-orchestration

- [x] 8.1 Тесты: один активный эпизод на ветку, фиксация шага до действия, предложение вне КИК отклоняется, replay без повторной отправки, устойчивое закрытие (идемпотентно), исход/stop_reason раздельны
- [x] 8.2 Порт `ExecutionEngine`; локальный детерминированный движок эпизода; контроллеры кампании/эпизода, связывающие память+LLM+инструменты+бюджеты; тесты зелёные

## 9. state-checkpoint

- [x] 9.1 Тесты: барьер/drain, UNKNOWN блокирует READY, манифест+READY-head, BLOCKED без адаптера СС, форк не видит будущее родителя, расход не откатывается
- [x] 9.2 Порт `ArtifactStore` (content-addressed) + `TargetSnapshotAdapter`; локальный fs-store; протокол чекпоинта/resume/fork с lineage; тесты зелёные

## 10. operator-api

- [x] 10.1 Тесты: идемпотентность команды по command_id, CONFLICT, авторизация read, отсутствие секретов в экспорте
- [x] 10.2 FastAPI-приложение: команды через контроллеры + outbox, read-модели, порт `AuthProvider`; тесты зелёные (httpx ASGI)

## 11. Production-адаптеры (источник истины)

- [x] 11.1 PostgreSQL-адаптеры (SQLAlchemy) для MemoryStore/BudgetStore/CallJournal + схемы/миграции; интеграционные тесты за маркером (skip без БД)
- [x] 11.2 Прогон доменных контрактных тестов на PG-адаптере (параметризация фикстурой адаптера)

## 12. Сборка, демо и проверка

- [x] 12.1 Локальный демо-прогон кампании «end-to-end» на in-memory адаптерах (CLI `python -m attacking_agent.demo`)
- [x] 12.2 Полный прогон `pytest`, `ruff`, `mypy`; README с запуском и границами MVP; `openspec validate --strict` зелёный

## Workflow follow-up

- Архивировать change `ag-harness-core` (`openspec archive`) после подтверждения полной реализации.
