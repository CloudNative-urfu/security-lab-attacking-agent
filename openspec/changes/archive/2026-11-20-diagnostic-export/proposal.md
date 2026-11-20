# Proposal

## Why

Когда у СС нет проверенного snapshot/restore-адаптера, полный чекпоинт АГ+СС честно
возвращает BLOCKED. Но для анализа нужен отдельный **диагностический экспорт** состояния
АГ, который явно НЕ обещает восстановление АГ+СС (чтобы BLOCKED не выдавали за downgrade).
Этого пути не было в реализации.

## What Changes

- Вводится `diagnostic_export`: снимок состояния АГ (память ветки) в content-addressed
  store, помеченный `diagnostic_only=True`, `restorable=False`. Работает БЕЗ адаптера СС.

## Capabilities

### Modified Capabilities

- `state-checkpoint`: диагностический экспорт АГ, не являющийся полным чекпоинтом.

## Impact

- Расширение `CheckpointManager` (метод `diagnostic_export`) и моделей.
