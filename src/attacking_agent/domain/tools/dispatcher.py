"""Диспетчер инструментов — единственный путь исполнения.

Любой вызов инструмента проходит проверку контракта параметров, ограничений политики,
резервирование бюджета и запись durable intent до внешней отправки; результат
записывается независимо от извлечённого знания. Повторная доставка того же
(op_id, attempt_id) не вызывает вторую физическую отправку.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic import ValidationError as PydanticValidationError

from attacking_agent.domain.budget import (
    BudgetAccountRef,
    LedgerManager,
    ReserveStatus,
    StartStatus,
)
from attacking_agent.domain.common.scope import Scope
from attacking_agent.domain.policy import Decision, PolicyEnforcer
from attacking_agent.domain.tools.contracts import ToolRegistry
from attacking_agent.domain.tools.journal import (
    CallJournal,
    IntentRecord,
    Observation,
    ObservationStatus,
    ResultRecord,
)
from attacking_agent.domain.tools.runner import RunRequest, ToolRunner


class DispatchStatus(StrEnum):
    OK = "ok"
    INVALID = "invalid"
    DENIED = "denied"
    NO_BUDGET = "no_budget"
    UNKNOWN = "unknown"
    ERROR = "error"


class DispatchRequest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    op_id: str
    attempt_id: str
    tool_name: str
    params: dict[str, Any]
    scope: Scope
    account_ref: BudgetAccountRef
    reserve_amount: Decimal
    reservation_id: str


@dataclass(frozen=True, slots=True)
class DispatchResult:
    status: DispatchStatus
    observation: Observation | None = None
    reason: str = ""


_STATUS_TO_DISPATCH = {
    ObservationStatus.OK: DispatchStatus.OK,
    ObservationStatus.REFUSED: DispatchStatus.OK,
    ObservationStatus.UNKNOWN: DispatchStatus.UNKNOWN,
    ObservationStatus.ERROR: DispatchStatus.ERROR,
}


class Dispatcher:
    def __init__(
        self,
        policy: PolicyEnforcer,
        ledger: LedgerManager,
        journal: CallJournal,
        runner: ToolRunner,
        registry: ToolRegistry,
    ) -> None:
        self._policy = policy
        self._ledger = ledger
        self._journal = journal
        self._runner = runner
        self._registry = registry

    def dispatch(self, req: DispatchRequest) -> DispatchResult:
        # 0. Идемпотентность: повтор доставки не вызывает вторую отправку.
        prior = self._journal.get_result(req.op_id, req.attempt_id)
        if prior is not None:
            return DispatchResult(
                DispatchStatus(prior.dispatch_status), prior.observation, "повтор доставки"
            )

        contract = self._registry.get(req.tool_name)
        if contract is None:
            return DispatchResult(DispatchStatus.INVALID, reason=f"нет контракта {req.tool_name}")

        # 1. Валидация параметров до любой отправки.
        try:
            params = contract.params_model.model_validate(req.params)
        except PydanticValidationError as exc:
            return DispatchResult(DispatchStatus.INVALID, reason=str(exc))

        # 2. Политика: операция и (если есть) адрес.
        op_res = self._policy.check_operation(contract.operation)
        if op_res.decision is Decision.DENIED:
            return DispatchResult(DispatchStatus.DENIED, reason=op_res.reason)
        address: str | None = None
        if contract.address_field is not None:
            address = str(getattr(params, contract.address_field))
            addr_res = self._policy.check_address(address)
            if addr_res.decision is Decision.DENIED:
                return DispatchResult(DispatchStatus.DENIED, reason=addr_res.reason)

        # 3. Резерв и permit на одну попытку.
        reserve = self._ledger.reserve(req.account_ref, req.reserve_amount, req.reservation_id)
        if reserve.status is not ReserveStatus.OK:
            return DispatchResult(DispatchStatus.NO_BUDGET, reason=reserve.status.value)
        start = self._ledger.start(req.reservation_id, req.attempt_id)
        if start.status is not StartStatus.OK:
            return DispatchResult(DispatchStatus.NO_BUDGET, reason=start.status.value)

        # 4. Durable intent — до внешней отправки.
        self._journal.record_intent(
            IntentRecord(
                op_id=req.op_id,
                attempt_id=req.attempt_id,
                branch_id=req.scope.branch_id,
                tool_name=req.tool_name,
                permit_id=start.permit_id,
            )
        )

        # 5. Исполнение в изолированном runner.
        observation = self._runner.run(
            RunRequest(executor=contract.executor, params=params, address=address)
        )

        # 6. Результат фиксируется всегда.
        status = _STATUS_TO_DISPATCH[observation.status]
        self._journal.record_result(
            ResultRecord(
                op_id=req.op_id,
                attempt_id=req.attempt_id,
                branch_id=req.scope.branch_id,
                observation=observation,
                dispatch_status=status.value,
            )
        )

        # 7. Учёт расхода: UNKNOWN удерживается, иначе фиксируется actual.
        charge_id = f"chg-{req.attempt_id}"
        if observation.status is ObservationStatus.UNKNOWN:
            self._ledger.settle(req.attempt_id, charge_id, None)
        else:
            self._ledger.settle(req.attempt_id, charge_id, req.reserve_amount)

        return DispatchResult(status, observation, observation.error or "")

    def has_unresolved_unknown(self, scope: Scope) -> bool:
        return bool(self._journal.unresolved_unknowns(scope.branch_id))

    def reconcile(self, op_id: str, attempt_id: str, final_status: ObservationStatus) -> None:
        self._journal.reconcile(op_id, attempt_id, final_status)
