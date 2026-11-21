"""Исполнитель одного шага эпизода (общий для in-process контроллера и Temporal-активностей).

Шаг разбит на три операции ввода-вывода с сериализуемыми результатами:
- ``propose``  — чтение опыта, стратегии, предложение модели;
- ``act``      — проверка области, паспорт шага, вызов инструмента, фиксация наблюдения;
- ``learn``    — обучение библиотеки стратегий по исходу.

Это позволяет собрать тонкий durable-граф: цикл эпизода — детерминированный Workflow, а
каждая из этих операций — отдельная Temporal Activity (устойчивость по шагам). Повтор шага
безопасен за счёт детерминированных op/attempt id и claim в журнале.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from attacking_agent.domain.budget import BudgetAccountRef
from attacking_agent.domain.llm import LLMProxy, LLMRequest, ProxyStatus
from attacking_agent.domain.memory import EventInput, Reader, ReadMode, Writer
from attacking_agent.domain.memory.roles import AppendStatus
from attacking_agent.domain.orchestration.metrics import MetricsControl
from attacking_agent.domain.orchestration.models import EpisodeSpec, StepDecision
from attacking_agent.domain.orchestration.prompt import build_messages
from attacking_agent.domain.policy import Decision, PolicyEnforcer
from attacking_agent.domain.strategy import StrategyLibrary
from attacking_agent.domain.tools import (
    Dispatcher,
    DispatchRequest,
    DispatchStatus,
)


def step_hash(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class ProposeResult:
    ok: bool
    llm_status: str
    kind: str = ""
    outcome: str = "INCONCLUSIVE"
    operation: str = ""
    tool: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    hypothesis: str = ""
    used_strategy_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ActResult:
    status: str  # ok | out_of_scope | passport_conflict | unknown | error
    observation_payload: str | None = None


@dataclass(frozen=True, slots=True)
class LoopAction:
    terminal: bool
    stop_reason: str


# Синглтон-сигналы детерминированного управления циклом (общие для контроллера и Workflow).
PROCEED_TO_ACT = LoopAction(False, "__act__")
_NEED_EVIDENCE = LoopAction(False, "need_evidence")


def decide_after_propose(pr: ProposeResult) -> LoopAction:
    """Детерминированное решение после предложения модели."""
    if not pr.ok:
        return LoopAction(True, f"llm_{pr.llm_status}")
    if pr.kind == "FINISH":
        return LoopAction(True, "finished")
    if pr.kind == "INCONCLUSIVE":
        return LoopAction(True, "inconclusive")
    if pr.kind == "NEED_EVIDENCE":
        return _NEED_EVIDENCE
    return PROCEED_TO_ACT


def decide_after_act(ar: ActResult) -> str | None:
    """Детерминированное решение после действия; None — продолжать цикл."""
    if ar.status == "out_of_scope":
        return "out_of_scope"
    if ar.status == "passport_conflict":
        return "passport_conflict"
    if ar.status == "unknown":
        return "unknown_blocked"
    return None


class EpisodeStepExecutor:
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
        max_tokens: int = 1024,
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
        # Reasoning-моделям нужен запас токенов на «размышление» перед финальным JSON.
        self._max_tokens = max_tokens

    def propose(self, spec: EpisodeSpec, step: int, observations: list[str]) -> ProposeResult:
        read = self._reader.read(spec.scope, ReadMode.TASK, spec.question, limit=20)
        evidence = [k.statement for k in read.knowledge]

        strategies: list[str] = []
        used_ids: list[str] = []
        if self._strategies is not None:
            recs = self._strategies.retrieve(spec.scope, spec.question, limit=5)
            strategies = [r.spec.technique for r in recs]
            used_ids = [r.strategy_id for r in recs]

        ep = spec.episode_id
        req = LLMRequest(
            op_id=f"{ep}-llm-op-{step}",
            attempt_id=f"{ep}-llm-att-{step}",
            initiator="episode-controller",
            model_ref="model-x",
            messages=build_messages(spec.question, evidence, observations, strategies),
            max_tokens=self._max_tokens,
        )
        result = self._proxy.request(
            req,
            account_ref=self._llm_account,
            reserve_amount=self._llm_reserve,
            reservation_id=f"{ep}-llm-res-{step}",
            schema=StepDecision,
        )
        if result.status is ProxyStatus.OK and isinstance(result.structured, StepDecision):
            d = result.structured
            return ProposeResult(
                ok=True,
                llm_status=result.status.value,
                kind=d.kind,
                outcome=d.outcome,
                operation=d.operation,
                tool=d.tool,
                params=dict(d.params),
                hypothesis=d.hypothesis,
                used_strategy_ids=used_ids,
            )
        return ProposeResult(ok=False, llm_status=result.status.value, used_strategy_ids=used_ids)

    def act(self, spec: EpisodeSpec, step: int, decision: ProposeResult) -> ActResult:
        ep = spec.episode_id
        if self._policy.check_operation(decision.operation).decision is Decision.DENIED:
            return ActResult("out_of_scope")

        passport = EventInput(
            event_id=f"{ep}-passport-{step}",
            hash=step_hash(ep, step, "passport", decision.tool, sorted(decision.params.items())),
            source="episode-controller",
            kind="step",
            payload={"tool": decision.tool, "hypothesis": decision.hypothesis},
            episode_id=ep,
        )
        if self._writer.append_event(spec.scope, passport).status is AppendStatus.CONFLICT:
            return ActResult("passport_conflict")

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
        payload_json: str | None = None
        if dr.observation is not None:
            self._writer.append_event(
                spec.scope,
                EventInput(
                    event_id=f"{ep}-obs-{step}",
                    hash=step_hash(ep, step, "obs", dr.status.value),
                    source="tool",
                    kind="observation",
                    payload=dr.observation.payload,
                    episode_id=ep,
                    op_id=f"{ep}-op-{step}",
                ),
            )
            self._metrics.record(dr.observation)
            payload_json = json.dumps(dr.observation.payload, ensure_ascii=False)

        status = "unknown" if dr.status is DispatchStatus.UNKNOWN else "ok"
        return ActResult(status, payload_json)

    def learn(self, spec: EpisodeSpec, supported: bool, strategy_ids: list[str]) -> None:
        if self._strategies is None or not strategy_ids:
            return
        for sid in strategy_ids:
            self._strategies.note_outcome(spec.scope, sid, supported=supported)
