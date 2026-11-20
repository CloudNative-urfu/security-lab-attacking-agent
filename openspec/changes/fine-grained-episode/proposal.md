# Proposal

## Why

Прежний Temporal-адаптер был крупнозернистым: эпизод = одна Activity. Нужна тонкая
разбивка по v8 (G5): эпизод как детерминированный Workflow с отдельной durable Activity на
каждый шаг (propose/act/learn) — пошаговая устойчивость и видимость, при этом безопасность
повтора обеспечивает идемпотентность харнеса.

## What Changes

- Логика шага вынесена в `EpisodeStepExecutor` (propose/act/learn, сериализуемые результаты);
  `EpisodeController` переведён на него (поведение неизменно).
- Доменный тонкий драйвер `run_episode_steps` + `StepActivities` (без Temporal) — эквивалентен
  монолитному контроллеру (доказано без Temporal).
- Temporal: `FineGrainedEpisodeWorkflow` (цикл) + пошаговые Activity (ep_propose/ep_act/
  ep_learn) + `FineGrainedTemporalEngine`.

## Capabilities

### Modified Capabilities

- `campaign-orchestration`: эпизод исполняется как тонкий durable-граф с пошаговыми Activity.

## Impact

- Новые модули orchestration (step_executor, fine_grained) и адаптеры Temporal; домен без temporalio.
