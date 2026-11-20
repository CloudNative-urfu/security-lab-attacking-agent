"""Контроллеры кампании и эпизода.

Контроллер эпизода ведёт детерминированный цикл: чтение опыта → предложение модели →
фиксация паспорта шага (до действия) → вызов инструмента → наблюдение → метрики →
решение. Контроллер кампании допускает один активный эпизод на ветку и принимает
закрытие ровно один раз. Идентификаторы шага детерминированы, поэтому повторный прогон
не вызывает повторную физическую отправку (дедупликация в диспетчере/журнале).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from attacking_agent.domain.budget import BudgetAccountRef
from attacking_agent.domain.common.scope import Scope
from attacking_agent.domain.llm import LLMProxy
from attacking_agent.domain.memory import Reader, Writer
from attacking_agent.domain.orchestration.metrics import MetricsControl
from attacking_agent.domain.orchestration.models import (
    EpisodeOutcome,
    EpisodeResult,
    EpisodeSpec,
)
from attacking_agent.domain.orchestration.step_executor import (
    PROCEED_TO_ACT,
    EpisodeStepExecutor,
    decide_after_act,
    decide_after_propose,
)
from attacking_agent.domain.policy import PolicyEnforcer
from attacking_agent.domain.strategy import StrategyLibrary
from attacking_agent.domain.tools import Dispatcher


class OpenStatus(StrEnum):
    OK = "ok"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class OpenResult:
    status: OpenStatus


@dataclass(frozen=True, slots=True)
class CloseResult:
    accepted: bool
    outcome: EpisodeOutcome
    stop_reason: str


def _branch_key(scope: Scope) -> str:
    return f"{scope.campaign_id}:{scope.branch_id}:{scope.target_ref}"


class CampaignController:
    """Жизненный цикл кампании: один активный эпизод на ветку, идемпотентное закрытие."""

    def __init__(self) -> None:
        self._active: dict[str, str] = {}
        self._closed: dict[str, CloseResult] = {}

    def open_episode(self, scope: Scope, episode_id: str) -> OpenResult:
        key = _branch_key(scope)
        active = self._active.get(key)
        if active is not None and active != episode_id:
            return OpenResult(OpenStatus.REJECTED)
        self._active[key] = episode_id
        return OpenResult(OpenStatus.OK)

    def close_episode(self, scope: Scope, episode_id: str) -> None:
        key = _branch_key(scope)
        if self._active.get(key) == episode_id:
            del self._active[key]

    def accept_closed(
        self, episode_id: str, outcome: EpisodeOutcome, stop_reason: str
    ) -> CloseResult:
        prior = self._closed.get(episode_id)
        if prior is not None:
            return CloseResult(False, prior.outcome, prior.stop_reason)
        result = CloseResult(True, outcome, stop_reason)
        self._closed[episode_id] = result
        return result


class EpisodeController:
    """Детерминированный цикл эпизода."""

    def __init__(
        self,
        reader: Reader,
        writer: Writer,
        proxy: LLMProxy,
        dispatcher: Dispatcher,
        policy: PolicyEnforcer,
        metrics: MetricsControl,
        llm_account: BudgetAccountRef,
        tool_account: BudgetAccountRef,
        llm_reserve: Decimal,
        tool_reserve: Decimal,
        strategy_library: StrategyLibrary | None = None,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._proxy = proxy
        self._dispatcher = dispatcher
        self._policy = policy
        self._metrics = metrics
        self._llm_account = llm_account
        self._tool_account = tool_account
        self._llm_reserve = llm_reserve
        self._tool_reserve = tool_reserve
        self._strategies = strategy_library
        self._executor = EpisodeStepExecutor(
            reader=reader,
            writer=writer,
            proxy=proxy,
            dispatcher=dispatcher,
            policy=policy,
            metrics=metrics,
            llm_account=llm_account,
            tool_account=tool_account,
            llm_reserve=llm_reserve,
            tool_reserve=tool_reserve,
            strategy_library=strategy_library,
        )

    def run(self, spec: EpisodeSpec) -> EpisodeResult:
        outcome = EpisodeOutcome.INCONCLUSIVE
        stop_reason = "step_limit"
        steps_taken = 0
        observations: list[str] = []  # недоверенные наблюдения СС для контекста
        used_strategy_ids: set[str] = set()

        for step in range(spec.max_steps):
            steps_taken = step + 1
            pr = self._executor.propose(spec, step, observations)
            used_strategy_ids.update(pr.used_strategy_ids)

            action = decide_after_propose(pr)
            if action is not PROCEED_TO_ACT:
                if action.terminal:
                    outcome = EpisodeOutcome(pr.outcome) if pr.kind == "FINISH" else outcome
                    stop_reason = action.stop_reason
                    break
                continue  # NEED_EVIDENCE

            ar = self._executor.act(spec, step, pr)
            if ar.observation_payload is not None:
                observations.append(ar.observation_payload)
            after = decide_after_act(ar)
            if after is not None:
                stop_reason = after
                break

        self._executor.learn(
            spec, outcome is EpisodeOutcome.SUPPORTED, list(used_strategy_ids)
        )
        return EpisodeResult(outcome, stop_reason, steps_taken)
