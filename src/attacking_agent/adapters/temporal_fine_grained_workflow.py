"""Тонкий Workflow эпизода: детерминированный цикл с пошаговыми Activity.

Минимальные импорты (Temporal sandbox). Каждый шаг — отдельная durable Activity (propose/
act/learn), вызываемая по имени. Ветвление детерминировано (сравнения строк-примитивов).
При сбое worker Temporal проигрывает Workflow и повторяет только незавершённые Activity;
завершённые шаги берутся из истории, а повтор физических отправок исключён идемпотентностью
харнеса (детерминированные op/attempt id + claim в журнале).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

_TIMEOUT = timedelta(seconds=30)
_RETRY = RetryPolicy(initial_interval=timedelta(milliseconds=200), maximum_attempts=3)


@workflow.defn
class FineGrainedEpisodeWorkflow:
    @workflow.run
    async def run(self, spec: dict[str, Any]) -> dict[str, Any]:
        max_steps = int(spec["max_steps"])
        observations: list[str] = []
        used_ids: list[str] = []
        outcome = "INCONCLUSIVE"
        stop_reason = "step_limit"
        steps_taken = 0

        for step in range(max_steps):
            steps_taken = step + 1
            pr = await workflow.execute_activity(
                "ep_propose",
                {**spec, "step": step, "observations": observations},
                start_to_close_timeout=_TIMEOUT,
                retry_policy=_RETRY,
            )
            for sid in pr["used_strategy_ids"]:
                if sid not in used_ids:
                    used_ids.append(sid)

            if pr["refusal"]:
                stop_reason = "model_refused"
                break
            if not pr["ok"]:
                stop_reason = f"llm_{pr['llm_status']}"
                break
            if pr["kind"] == "FINISH":
                outcome = pr["outcome"]
                stop_reason = "finished"
                break
            if pr["kind"] == "INCONCLUSIVE":
                stop_reason = "inconclusive"
                break
            if pr["kind"] == "NEED_EVIDENCE":
                continue

            ar = await workflow.execute_activity(
                "ep_act",
                {**spec, "step": step, "decision": pr},
                start_to_close_timeout=_TIMEOUT,
                retry_policy=_RETRY,
            )
            if ar["observation_payload"] is not None:
                observations.append(ar["observation_payload"])
            status = ar["status"]
            if status in ("out_of_scope", "passport_conflict"):
                stop_reason = status
                break
            if status == "unknown":
                stop_reason = "unknown_blocked"
                break

        await workflow.execute_activity(
            "ep_learn",
            {**spec, "supported": outcome == "SUPPORTED", "strategy_ids": used_ids},
            start_to_close_timeout=_TIMEOUT,
            retry_policy=_RETRY,
        )
        return {"outcome": outcome, "stop_reason": stop_reason, "steps_taken": steps_taken}
