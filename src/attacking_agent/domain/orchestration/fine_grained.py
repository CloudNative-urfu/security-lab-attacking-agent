"""Тонкий граф эпизода: шаг как отдельная операция (propose/act/learn).

Доменная часть без Temporal: сериализуемые операции шага и детерминированный цикл-драйвер
``run_episode_steps``. Тот же цикл воспроизводит Temporal Workflow (адаптер), где каждая
операция — отдельная durable Activity. Поэтому эквивалентность монолитному контроллеру
доказывается без Temporal, а устойчивость по шагам — на реальном Temporal.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from attacking_agent.domain.common.ids import BranchId, CampaignId
from attacking_agent.domain.common.scope import Scope
from attacking_agent.domain.orchestration.models import EpisodeSpec
from attacking_agent.domain.orchestration.step_executor import (
    EpisodeStepExecutor,
    ProposeResult,
)


def spec_to_dict(spec: EpisodeSpec) -> dict[str, Any]:
    return {
        "episode_id": spec.episode_id,
        "campaign_id": spec.scope.campaign_id,
        "branch_id": spec.scope.branch_id,
        "target_ref": spec.scope.target_ref,
        "question": spec.question,
        "max_steps": spec.max_steps,
    }


def dict_to_spec(d: dict[str, Any]) -> EpisodeSpec:
    return EpisodeSpec(
        episode_id=str(d["episode_id"]),
        scope=Scope(
            campaign_id=CampaignId(str(d["campaign_id"])),
            branch_id=BranchId(str(d["branch_id"])),
            target_ref=str(d["target_ref"]),
        ),
        question=str(d["question"]),
        max_steps=int(d["max_steps"]),
    )


class StepActivities:
    """Сериализуемые операции шага поверх :class:`EpisodeStepExecutor` (без Temporal)."""

    def __init__(self, executor: EpisodeStepExecutor) -> None:
        self._executor = executor

    def propose(self, payload: dict[str, Any]) -> dict[str, Any]:
        spec = dict_to_spec(payload)
        pr = self._executor.propose(spec, int(payload["step"]), list(payload["observations"]))
        return asdict(pr)

    def act(self, payload: dict[str, Any]) -> dict[str, Any]:
        spec = dict_to_spec(payload)
        decision = ProposeResult(**payload["decision"])
        ar = self._executor.act(spec, int(payload["step"]), decision)
        return asdict(ar)

    def learn(self, payload: dict[str, Any]) -> dict[str, Any]:
        spec = dict_to_spec(payload)
        self._executor.learn(spec, bool(payload["supported"]), list(payload["strategy_ids"]))
        return {}


def run_episode_steps(activities: StepActivities, spec_dict: dict[str, Any]) -> dict[str, Any]:
    """Детерминированный цикл эпизода, вызывающий пошаговые операции. Возвращает результат dict.

    Тот же поток воспроизводит Temporal Workflow (через await execute_activity).
    """
    max_steps = int(spec_dict["max_steps"])
    observations: list[str] = []
    used_ids: set[str] = set()
    outcome = "INCONCLUSIVE"
    stop_reason = "step_limit"
    steps_taken = 0

    for step in range(max_steps):
        steps_taken = step + 1
        pr = activities.propose({**spec_dict, "step": step, "observations": observations})
        used_ids.update(pr["used_strategy_ids"])

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

        ar = activities.act({**spec_dict, "step": step, "decision": pr})
        if ar["observation_payload"] is not None:
            observations.append(ar["observation_payload"])
        status = ar["status"]
        if status in ("out_of_scope", "passport_conflict"):
            stop_reason = status
            break
        if status == "unknown":
            stop_reason = "unknown_blocked"
            break

    activities.learn(
        {**spec_dict, "supported": outcome == "SUPPORTED", "strategy_ids": list(used_ids)}
    )
    return {"outcome": outcome, "stop_reason": stop_reason, "steps_taken": steps_taken}
