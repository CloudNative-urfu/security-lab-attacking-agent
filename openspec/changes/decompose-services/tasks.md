# Tasks

## 1. LLM-шлюз (сервис + сетевой адаптер)

- [ ] 1.1 Тесты: домен (`LLMProxy`) работает через `HttpLLMClient` идентично локальному (ASGI in-process); одна отправка; usage/cost сохранены
- [ ] 1.2 Сервис `services/llm_gateway` (FastAPI `POST /v1/complete`) поверх `LLMClient` (LiteLLM/fake)
- [ ] 1.3 Адаптер `HttpLLMClient` (реализует `LLMClient`); Decimal-cost без потери точности

## 2. Tool-runner (сервис + сетевой адаптер)

- [ ] 2.1 Тесты: диспетчер исполняет инструмент через `HttpToolRunner`; инструмент вне allowlist отклонён; cap/таймаут сохранены
- [ ] 2.2 Сервис `services/tool_runner` (FastAPI `POST /v1/run`) с allowlist и `SubprocessToolRunner` внутри
- [ ] 2.3 Адаптер `HttpToolRunner` (реализует `ToolRunner`): передаёт идентичность инструмента и параметры

## 3. Деплой и топология

- [ ] 3.1 Entrypoints `services/api` (операторский API) и `services/worker` (Temporal-воркер)
- [ ] 3.2 Dockerfile(ы) и сервисы в docker-compose (llm-gateway, tool-runner, api, worker)
- [ ] 3.3 README: диаграмма топологии и запуск сервисов

## 4. Приёмка

- [ ] 4.1 Полный прогон ворот; контрактные тесты зелёные; архивировать change
