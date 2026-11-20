"""Контроллеры кампании и эпизода.

Контроллер эпизода ведёт детерминированный цикл: чтение опыта → предложение модели →
фиксация паспорта шага (до действия) → вызов инструмента → наблюдение → метрики →
решение. Контроллер кампании допускает один активный эпизод на ветку и принимает
закрытие ровно один раз. Идентификаторы шага детерминированы, поэтому повторный прогон
не вызывает повторную физическую отправку (дедупликация в диспетчере/журнале).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from attacking_agent.domain.budget import BudgetAccountRef
from attacking_agent.domain.common.scope import Scope
from attacking_agent.domain.llm import LLMProxy, LLMRequest, ProxyStatus
from attacking_agent.domain.memory import EventInput, Reader, ReadMode, Writer
from attacking_agent.domain.memory.roles import AppendStatus
from attacking_agent.domain.orchestration.metrics import MetricsControl
from attacking_agent.domain.orchestration.models import (
    EpisodeOutcome,
    EpisodeResult,
    EpisodeSpec,
    StepDecision,
)
from attacking_agent.domain.orchestration.prompt import build_messages
from attacking_agent.domain.policy import Decision, PolicyEnforcer
from attacking_agent.domain.strategy import StrategyLibrary
from attacking_agent.domain.tools import (
    Dispatcher,
    DispatchRequest,
    DispatchStatus,
)


def _hash(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


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

    def run(self, spec: EpisodeSpec) -> EpisodeResult:
        ep = spec.episode_id
        outcome = EpisodeOutcome.INCONCLUSIVE
        stop_reason = "step_limit"
        steps_taken = 0
        observations: list[str] = []  # недоверенные наблюдения СС для контекста
        used_strategy_ids: set[str] = set()

        for step in range(spec.max_steps):
            steps_taken = step + 1

            # 1. Чтение опыта (TASK) — свидетельства идут в контекст решения.
            read = self._reader.read(spec.scope, ReadMode.TASK, spec.question, limit=20)
            evidence = [k.statement for k in read.knowledge]

            # 1b. Релевантные стратегии (доверенная подсказка).
            strategies: list[str] = []
            if self._strategies is not None:
                recs = self._strategies.retrieve(spec.scope, spec.question, limit=5)
                strategies = [r.spec.technique for r in recs]
                used_strategy_ids.update(r.strategy_id for r in recs)

            # 2. Предложение модели (под допуском и учётом расхода).
            decision, pstatus = self._propose(spec, step, evidence, observations, strategies)
            if decision is None:
                stop_reason = f"llm_{pstatus.value}"
                break

            if decision.kind == "FINISH":
                outcome = EpisodeOutcome(decision.outcome)
                stop_reason = "finished"
                break
            if decision.kind == "INCONCLUSIVE":
                outcome = EpisodeOutcome.INCONCLUSIVE
                stop_reason = "inconclusive"
                break
            if decision.kind == "NEED_EVIDENCE":
                continue

            # 3. ACTION: проверка области -> паспорт шага -> действие.
            if self._policy.check_operation(decision.operation).decision is Decision.DENIED:
                stop_reason = "out_of_scope"
                break

            passport = EventInput(
                event_id=f"{ep}-passport-{step}",
                hash=_hash(ep, step, "passport", decision.tool, sorted(decision.params.items())),
                source="episode-controller",
                kind="step",
                payload={"tool": decision.tool, "hypothesis": decision.hypothesis},
                episode_id=ep,
            )
            append = self._writer.append_event(spec.scope, passport)
            if append.status is AppendStatus.CONFLICT:
                stop_reason = "passport_conflict"
                break

            # 4. Действие через диспетчер (идемпотентно по детерминированным id).
            dr = self._dispatcher.dispatch(
                DispatchRequest(
                    op_id=f"{ep}-op-{step}",
                    attempt_id=f"{ep}-att-{step}",
                    tool_name=decision.tool,
                    params=decision.params,
                    scope=spec.scope,
                    account_ref=self._tool_account,
                    reserve_amount=self._tool_reserve,
                    reservation_id=f"{ep}-tres-{step}",
                )
            )

            # 5. Наблюдение фиксируется как Event (независимо от знания) и подаётся в
            # контекст следующего шага как НЕДОВЕРЕННЫЕ данные.
            if dr.observation is not None:
                self._writer.append_event(
                    spec.scope,
                    EventInput(
                        event_id=f"{ep}-obs-{step}",
                        hash=_hash(ep, step, "obs", dr.status.value),
                        source="tool",
                        kind="observation",
                        payload=dr.observation.payload,
                        episode_id=ep,
                        op_id=f"{ep}-op-{step}",
                    ),
                )
                self._metrics.record(dr.observation)
                observations.append(json.dumps(dr.observation.payload, ensure_ascii=False))

            # 6. UNKNOWN блокирует зависимые действия.
            if dr.status is DispatchStatus.UNKNOWN:
                stop_reason = "unknown_blocked"
                break

        # Обучение библиотеки стратегий по исходу эпизода.
        if self._strategies is not None and used_strategy_ids:
            supported = outcome is EpisodeOutcome.SUPPORTED
            for sid in used_strategy_ids:
                self._strategies.note_outcome(spec.scope, sid, supported=supported)

        return EpisodeResult(outcome, stop_reason, steps_taken)

    def _propose(
        self,
        spec: EpisodeSpec,
        step: int,
        evidence: list[str],
        observations: list[str],
        strategies: list[str],
    ) -> tuple[StepDecision | None, ProxyStatus]:
        ep = spec.episode_id
        req = LLMRequest(
            op_id=f"{ep}-llm-op-{step}",
            attempt_id=f"{ep}-llm-att-{step}",
            initiator="episode-controller",
            model_ref="model-x",
            messages=build_messages(spec.question, evidence, observations, strategies),
            max_tokens=256,
        )
        result = self._proxy.request(
            req,
            account_ref=self._llm_account,
            reserve_amount=self._llm_reserve,
            reservation_id=f"{ep}-llm-res-{step}",
            schema=StepDecision,
        )
        if result.status is ProxyStatus.OK and isinstance(result.structured, StepDecision):
            return result.structured, result.status
        return None, result.status
