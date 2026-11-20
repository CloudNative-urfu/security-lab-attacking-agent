"""LLM Proxy — единый доверенный доступ к модели.

Вызов модели выполняется только после выданного допуска (reserve/start); фактический
usage учитывается тем же ledger; скрытые retry/fallback отсутствуют — повтор является
новой учтённой попыткой. Структурированный ответ валидируется по схеме; смысл проверяет
инициатор, не парсер.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from attacking_agent.domain.budget import (
    BudgetAccountRef,
    LedgerManager,
    ReserveStatus,
    StartStatus,
)
from attacking_agent.domain.common.errors import BudgetError

# Исключения, означающие неоднозначный исход физической отправки.
_AMBIGUOUS_EXCEPTIONS = (TimeoutError,)


@dataclass(frozen=True, slots=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True, slots=True)
class LLMRequest:
    op_id: str
    attempt_id: str
    initiator: str
    model_ref: str
    messages: list[dict[str, str]]
    max_tokens: int


@dataclass(frozen=True, slots=True)
class LLMResponse:
    content: str
    structured: dict[str, Any] | None
    usage: Usage
    cost: Decimal


class LLMClient(Protocol):
    """Порт транспорта модели. Одна успешная ``complete`` = одна физическая отправка.

    Реализация обязана явно отключать provider/router retries и fallback.
    """

    def complete(self, request: LLMRequest) -> LLMResponse: ...


class ProxyStatus(StrEnum):
    OK = "ok"
    INVALID_OUTPUT = "invalid_output"
    DENIED = "denied"
    NO_BUDGET = "no_budget"
    ERROR = "error"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ProxyResult:
    status: ProxyStatus
    response: LLMResponse | None = None
    structured: BaseModel | None = None
    reason: str = ""


class LLMProxy:
    def __init__(self, client: LLMClient, ledger: LedgerManager) -> None:
        self._client = client
        self._ledger = ledger

    def request(
        self,
        req: LLMRequest,
        account_ref: BudgetAccountRef,
        reserve_amount: Decimal,
        reservation_id: str,
        schema: type[BaseModel] | None = None,
    ) -> ProxyResult:
        # 1. Допуск: без действующего permit запрос не отправляется (fail-closed:
        # любая ошибка допуска, включая отсутствие/мисконфиг счёта, блокирует отправку).
        try:
            reserve = self._ledger.reserve(account_ref, reserve_amount, reservation_id)
            if reserve.status is not ReserveStatus.OK:
                return ProxyResult(ProxyStatus.NO_BUDGET, reason=reserve.status.value)
            start = self._ledger.start(reservation_id, req.attempt_id)
            if start.status is not StartStatus.OK:
                return ProxyResult(ProxyStatus.DENIED, reason=start.status.value)
        except BudgetError as exc:
            return ProxyResult(ProxyStatus.DENIED, reason=str(exc))

        charge_id = f"chg-{req.attempt_id}"

        # 2. Одна физическая отправка, без скрытых retry/fallback.
        try:
            response = self._client.complete(req)
        except _AMBIGUOUS_EXCEPTIONS as exc:
            self._ledger.settle(req.attempt_id, charge_id, None)  # COST_UNKNOWN
            return ProxyResult(ProxyStatus.UNKNOWN, reason=str(exc))
        except Exception as exc:
            self._ledger.settle(req.attempt_id, charge_id, None)
            return ProxyResult(ProxyStatus.ERROR, reason=str(exc))

        # 3. Учёт фактического расхода.
        self._ledger.settle(req.attempt_id, charge_id, response.cost)

        # 4. Валидация структурированного ответа (смысл проверяет инициатор).
        if schema is not None:
            try:
                structured = schema.model_validate(response.structured or {})
            except PydanticValidationError as exc:
                return ProxyResult(ProxyStatus.INVALID_OUTPUT, response, reason=str(exc))
            return ProxyResult(ProxyStatus.OK, response, structured)

        return ProxyResult(ProxyStatus.OK, response)
