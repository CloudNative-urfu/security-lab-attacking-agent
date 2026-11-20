"""Workflow эпизода (отдельный модуль с минимальными импортами для Temporal sandbox).

Workflow детерминирован: он лишь оркеструет Activity ``run_episode`` (по имени, чтобы не
импортировать доменный код в sandbox) с таймаутом и политикой повторов. Вся работа эпизода
(LLM, инструменты, память) — внутри Activity, вне sandbox.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy


@workflow.defn
class EpisodeWorkflow:
    @workflow.run
    async def run(self, arg: dict[str, Any]) -> dict[str, Any]:
        timeout = float(arg.get("_timeout_seconds", 60.0))
        max_attempts = int(arg.get("_max_attempts", 3))
        result: dict[str, Any] = await workflow.execute_activity(
            "run_episode",
            arg,
            start_to_close_timeout=timedelta(seconds=timeout),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(milliseconds=200),
                maximum_attempts=max_attempts,
            ),
        )
        return result
