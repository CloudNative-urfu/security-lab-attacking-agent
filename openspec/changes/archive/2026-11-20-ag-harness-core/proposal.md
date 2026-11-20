# Proposal

## Why

Полигону нужен production-ready харнес атакующего агента (АГ): управляемая оболочка,
которая ведёт авторизованное исследование защит стороннего сервиса (СС) строго в
рамках КИК — бюджетов, политик (deny-by-default), областей (scope) и восстановимого
состояния. Безопасность системы определяется не моделью, а именно харнесом; поэтому
ядро инвариантов (допуск, учёт расходов, изоляция, атомарность памяти, чекпоинты)
должно быть реализовано и покрыто тестами до подключения тяжёлой инфраструктуры.

## What Changes

- Вводится доменное ядро харнеса на Python (hexagonal / ports-and-adapters): чистая
  доменная логика + подключаемые адаптеры инфраструктуры.
- Реализуется неизменяемая **КИК** с freeze и SHA-256 над канонизированным составом.
- Реализуется **менеджер бюджетов** (reserve → start → settle), Decimal-деньги,
  жёсткие/мягкие лимиты, типы (requests/tokens/time/money/episodes), COST_UNKNOWN и
  BOUND_BROKEN.
- Реализуется **проверка ограничений** (allowlist операций/адресов, версии правил,
  deny-by-default, повторная проверка адреса после DNS/redirect).
- Реализуется **диспетчер инструментов** с журналом вызовов (durable intent/attempt/
  result), раздельными op_id/attempt_id/reservation_id/charge_id и однократным permit;
  изолированный runner как порт.
- Реализуется **LLM Proxy** поверх LiteLLM без скрытых retry/fallback, с учётом usage и
  валидацией структурированного ответа.
- Реализуется **семантическая память** (Events append-only, Knowledge, Strategies,
  Edges) с ролями Reader (TASK/ANALYSIS), Writer (PREPARE→EVALUATE→COMMIT, candidate
  hash, атомарная публикация под branch fence) и Controller (применимость + курация).
- Реализуется **оркестрация кампании/эпизода**: детерминированный цикл эпизода и
  контроллер кампании с подключаемым движком исполнения (локальный in-process для MVP,
  порт для Temporal).
- Реализуется **чекпоинт/resume/fork** с манифестом, content-addressed артефактами,
  барьером/drain, READY-head и lineage; снимок СС — через порт адаптера (BLOCKED при
  отсутствии).
- Реализуется **операторский API** (FastAPI): командный путь (start/pause/resume/fork)
  и read-модели; аутентификация как порт.

## Capabilities

### New Capabilities

- `campaign-config`: неизменяемая конфигурация кампании (КИК), freeze, версия, хэш, refs на секреты.
- `policy-enforcement`: проверка ограничений и областей (scope), deny-by-default, версии правил.
- `budget-ledger`: лимиты и устойчивый учёт ресурсов, резерв/старт/расчёт, инциденты перерасхода.
- `tool-dispatch`: контракт и диспетчеризация инструментов, журнал вызовов, допуск и изоляция.
- `llm-proxy`: единый доверенный доступ к модели без скрытых повторов, с учётом расхода.
- `semantic-memory`: версионная память (Events/Knowledge/Strategies/Edges), Reader/Writer/Controller.
- `campaign-orchestration`: жизненный цикл кампании и детерминированный цикл эпизода.
- `state-checkpoint`: полный снимок состояния АГ+СС, возобновление и форк с lineage.
- `operator-api`: командный и read-интерфейс оператора с авторизацией.

### Modified Capabilities

<!-- Нет: это новая система. -->

## Impact

- Новый Python-пакет `attacking_agent` с подпакетами по capability; тесты `pytest`.
- Новые зависимости (families, точные версии фиксируются lock-ом): pydantic, sqlalchemy,
  psycopg, fastapi, httpx, litellm, opentelemetry-sdk; dev: pytest, ruff, mypy.
- Внешняя инфраструктура (PostgreSQL/pgvector, Temporal, S3, vLLM, gVisor runner, OIDC)
  подключается через порты; для MVP поставляются рабочие in-memory/локальные адаптеры.
