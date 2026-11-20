"""Движок тонкого durable-графа эпизода на Temporal.

Цикл эпизода исполняется как Temporal Workflow с отдельной Activity на каждый шаг
(propose/act/learn). Доменные операции (`StepActivities`) исполняются в процессе worker;
через границу Activity передаются только сериализуемые данные. Безопасность повтора шага —
идемпотентность харнеса (доказана отдельными тестами).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

from attacking_agent.adapters.temporal_fine_grained_workflow import FineGrainedEpisodeWorkflow
from attacking_agent.domain.orchestration import EpisodeOutcome, EpisodeResult, EpisodeSpec
from attacking_agent.domain.orchestration.fine_grained import StepActivities, spec_to_dict
from attacking_agent.domain.orchestration.step_executor import EpisodeStepExecutor


class TemporalStepActivities:
    """Temporal-обёртки пошаговых операций поверх доменных `StepActivities`."""

    def __init__(self, executor: EpisodeStepExecutor) -> None:
        self._acts = StepActivities(executor)

    @activity.defn(name="ep_propose")
    def ep_propose(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._acts.propose(payload)

    @activity.defn(name="ep_act")
    def ep_act(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._acts.act(payload)

    @activity.defn(name="ep_learn")
    def ep_learn(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._acts.learn(payload)


class FineGrainedTemporalEngine:
    def __init__(self, client: Client, task_queue: str) -> None:
        self._client = client
        self._task_queue = task_queue

    async def execute(self, executor: EpisodeStepExecutor, spec: EpisodeSpec) -> EpisodeResult:
        acts = TemporalStepActivities(executor)
        with ThreadPoolExecutor(max_workers=4) as pool:
            async with Worker(
                self._client,
                task_queue=self._task_queue,
                workflows=[FineGrainedEpisodeWorkflow],
                activities=[acts.ep_propose, acts.ep_act, acts.ep_learn],
                activity_executor=pool,
            ):
                out = await self._client.execute_workflow(
                    FineGrainedEpisodeWorkflow.run,
                    spec_to_dict(spec),
                    id=f"fg-{spec.episode_id}",
                    task_queue=self._task_queue,
                )
        return EpisodeResult(
            outcome=EpisodeOutcome(out["outcome"]),
            stop_reason=str(out["stop_reason"]),
            steps_taken=int(out["steps_taken"]),
        )
