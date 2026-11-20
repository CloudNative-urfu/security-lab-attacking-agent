"""Контракт capability llm-proxy."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

from attacking_agent.domain.budget import (
    BudgetAccountRef,
    InMemoryBudgetStore,
    LedgerManager,
)
from attacking_agent.domain.common.clock import ManualClock
from attacking_agent.domain.common.ids import SequentialIdFactory
from attacking_agent.domain.config import BudgetLevel, BudgetType, Hardness
from attacking_agent.domain.llm import (
    LLMProxy,
    LLMRequest,
    LLMResponse,
    ProxyStatus,
    Usage,
)


class _FakeClient:
    """Фейковый LLM-клиент: считает вызовы, без retry/fallback."""

    def __init__(self, responses: list[LLMResponse] | None = None) -> None:
        self.calls = 0
        self._responses = responses or []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        if self._responses:
            return self._responses.pop(0)
        return LLMResponse(
            content="предложение",
            structured={"hypothesis": "h1"},
            usage=Usage(prompt_tokens=100, completion_tokens=50),
            cost=Decimal("0.01"),
        )


class _ErrorClient:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        raise ConnectionError("провайдер недоступен")


class _Hypothesis(BaseModel):
    hypothesis: str


def _mgr() -> LedgerManager:
    return LedgerManager(
        InMemoryBudgetStore(), clock=ManualClock(), id_factory=SequentialIdFactory()
    )


def _account(mgr: LedgerManager, limit: str = "1.00", open_it: bool = True) -> BudgetAccountRef:
    ref = BudgetAccountRef(
        campaign_id="camp-1",
        level=BudgetLevel.CAMPAIGN,
        scope_key="camp-1",
        budget_type=BudgetType.MONEY,
    )
    if open_it:
        mgr.open_account(ref, limit=Decimal(limit), hardness=Hardness.HARD, currency="USD")
    return ref


def _req(attempt_id: str = "att-1") -> LLMRequest:
    return LLMRequest(
        op_id="op-1",
        attempt_id=attempt_id,
        initiator="episode-controller",
        model_ref="model-x",
        messages=[{"role": "user", "content": "предложи гипотезу"}],
        max_tokens=200,
    )


def test_request_without_permit_rejected() -> None:
    mgr = _mgr()
    ref = _account(mgr, open_it=False)  # счёт не открыт -> допуск невозможен
    client = _FakeClient()
    proxy = LLMProxy(client=client, ledger=mgr)
    res = proxy.request(
        _req(), account_ref=ref, reserve_amount=Decimal("0.05"), reservation_id="r1"
    )
    assert res.status in (ProxyStatus.DENIED, ProxyStatus.NO_BUDGET)
    assert client.calls == 0  # запрос не отправлен


def test_usage_accounted_in_ledger() -> None:
    mgr = _mgr()
    ref = _account(mgr)
    proxy = LLMProxy(client=_FakeClient(), ledger=mgr)
    res = proxy.request(
        _req(), account_ref=ref, reserve_amount=Decimal("0.05"), reservation_id="r1"
    )
    assert res.status is ProxyStatus.OK
    assert mgr.consumed(ref) == Decimal("0.01")


def test_no_hidden_retry_single_call() -> None:
    mgr = _mgr()
    ref = _account(mgr)
    client = _FakeClient()
    proxy = LLMProxy(client=client, ledger=mgr)
    proxy.request(_req(), account_ref=ref, reserve_amount=Decimal("0.05"), reservation_id="r1")
    assert client.calls == 1


def test_no_hidden_fallback_on_error() -> None:
    mgr = _mgr()
    ref = _account(mgr)
    client = _ErrorClient()
    proxy = LLMProxy(client=client, ledger=mgr)
    res = proxy.request(
        _req(), account_ref=ref, reserve_amount=Decimal("0.05"), reservation_id="r1"
    )
    assert res.status in (ProxyStatus.ERROR, ProxyStatus.UNKNOWN)
    assert client.calls == 1  # нет автоматического fallback на второго провайдера


def test_invalid_structured_output_flagged() -> None:
    mgr = _mgr()
    ref = _account(mgr)
    bad = LLMResponse(
        content="x",
        structured={"wrong_field": 1},
        usage=Usage(prompt_tokens=10, completion_tokens=5),
        cost=Decimal("0.002"),
    )
    proxy = LLMProxy(client=_FakeClient([bad]), ledger=mgr)
    res = proxy.request(
        _req(),
        account_ref=ref,
        reserve_amount=Decimal("0.05"),
        reservation_id="r1",
        schema=_Hypothesis,
    )
    assert res.status is ProxyStatus.INVALID_OUTPUT
    assert mgr.consumed(ref) == Decimal("0.002")  # расход всё равно учтён


def test_repair_is_separate_counted_attempt() -> None:
    mgr = _mgr()
    ref = _account(mgr)
    client = _FakeClient()
    proxy = LLMProxy(client=client, ledger=mgr)
    amt = Decimal("0.05")
    proxy.request(_req("att-1"), account_ref=ref, reserve_amount=amt, reservation_id="r1")
    proxy.request(_req("att-2"), account_ref=ref, reserve_amount=amt, reservation_id="r2")
    assert client.calls == 2
    assert mgr.consumed(ref) == Decimal("0.02")  # две учтённые попытки
