"""Production-адаптер порта :class:`ExecutionEngine` на Temporal.

Эпизод выполняется как Temporal Workflow, который вызывает Activity ``run_episode``;
Activity строит/использует доменный runner эпизода (``EpisodeController`` или мок) и
исполняет его. Temporal даёт устойчивую оркестрацию, таймаут и повтор; безопасность
повтора обеспечивает идемпотентность харнеса (детерминированные op/attempt id, claim
в журнале, идемпотентный резерв) — что доказано отдельными тестами.

Граница: сам runner исполняется внутри Activity в процессе worker (не сериализуется в
Temporal), поэтому worker запускается там же, где создан движок. Через границу Activity
передаётся только сериализуемая спецификация эпизода.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol

from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

from attacking_agent.adapters.temporal_workflow import EpisodeWorkflow
from attacking_agent.domain.common.ids import BranchId, CampaignId
from attacking_agent.domain.common.scope import Scope
from attacking_agent.domain.orchestration import EpisodeOutcome, EpisodeResult, EpisodeSpec


class EpisodeRunner(Protocol):
    """Структурный тип исполнителя эпизода (его реализует EpisodeController)."""

    def run(self, spec: EpisodeSpec) -> EpisodeResult: ...


def spec_to_arg(
    spec: EpisodeSpec, timeout_seconds: float, max_attempts: int
) -> dict[str, Any]:
    return {
        "episode_id": spec.episode_id,
        "campaign_id": spec.scope.campaign_id,
        "branch_id": spec.scope.branch_id,
        "target_ref": spec.scope.target_ref,
        "question": spec.question,
        "max_steps": spec.max_steps,
        "_timeout_seconds": timeout_seconds,
        "_max_attempts": max_attempts,
    }


def arg_to_spec(arg: dict[str, Any]) -> EpisodeSpec:
    return EpisodeSpec(
        episode_id=str(arg["episode_id"]),
        scope=Scope(
            campaign_id=CampaignId(str(arg["campaign_id"])),
            branch_id=BranchId(str(arg["branch_id"])),
            target_ref=str(arg["target_ref"]),
        ),
        question=str(arg["question"]),
        max_steps=int(arg["max_steps"]),
    )


def result_to_dict(result: EpisodeResult) -> dict[str, Any]:
    return {
        "outcome": result.outcome.value,
        "stop_reason": result.stop_reason,
        "steps_taken": result.steps_taken,
    }


def dict_to_result(data: dict[str, Any]) -> EpisodeResult:
    return EpisodeResult(
        outcome=EpisodeOutcome(data["outcome"]),
        stop_reason=str(data["stop_reason"]),
        steps_taken=int(data["steps_taken"]),
    )


class EpisodeActivities:
    """Activity-обёртка: исполняет доменный runner внутри Temporal Activity."""

    def __init__(self, runner: EpisodeRunner) -> None:
        self._runner = runner

    @activity.defn(name="run_episode")
    def run_episode(self, arg: dict[str, Any]) -> dict[str, Any]:
        spec = arg_to_spec(arg)
        return result_to_dict(self._runner.run(spec))


class TemporalExecutionEngine:
    """Реализация :class:`ExecutionEngine` поверх Temporal."""

    def __init__(
        self,
        client: Client,
        task_queue: str,
        timeout_seconds: float = 60.0,
        max_attempts: int = 3,
    ) -> None:
        self._client = client
        self._task_queue = task_queue
        self._timeout = timeout_seconds
        self._max_attempts = max_attempts

    async def execute(self, runner: EpisodeRunner, spec: EpisodeSpec) -> EpisodeResult:
        activities = EpisodeActivities(runner)
        with ThreadPoolExecutor(max_workers=4) as pool:
            async with Worker(
                self._client,
                task_queue=self._task_queue,
                workflows=[EpisodeWorkflow],
                activities=[activities.run_episode],
                activity_executor=pool,
            ):
                result = await self._client.execute_workflow(
                    EpisodeWorkflow.run,
                    spec_to_arg(spec, self._timeout, self._max_attempts),
                    id=f"episode-{spec.episode_id}",
                    task_queue=self._task_queue,
                )
        return dict_to_result(result)

    def run(self, controller: EpisodeRunner, spec: EpisodeSpec) -> EpisodeResult:
        """Синхронная реализация порта ExecutionEngine (запускает собственный loop)."""
        return asyncio.run(self.execute(controller, spec))
