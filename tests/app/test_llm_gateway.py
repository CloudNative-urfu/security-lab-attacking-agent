"""Контракт: домен (LLMProxy) через сетевой HttpLLMClient = локально (ASGI in-process)."""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient
from services.llm_gateway.app import create_gateway_app

from attacking_agent.adapters.http_llm import HttpLLMClient
from attacking_agent.domain.budget import (
    BudgetAccountRef,
    InMemoryBudgetStore,
    LedgerManager,
)
from attacking_agent.domain.common.clock import ManualClock
from attacking_agent.domain.common.ids import SequentialIdFactory
from attacking_agent.domain.config import BudgetLevel, BudgetType, Hardness
from attacking_agent.domain.llm import LLMProxy, LLMRequest, LLMResponse, ProxyStatus, Usage


class _FakeUnderlying:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            content="предложение",
            structured={"kind": "FINISH"},
            usage=Usage(prompt_tokens=100, completion_tokens=50),
            cost=Decimal("0.0125"),
        )


def _http_client(app) -> HttpLLMClient:
    return HttpLLMClient("http://gw", client=TestClient(app))


def _ledger() -> tuple[LedgerManager, BudgetAccountRef]:
    mgr = LedgerManager(
        InMemoryBudgetStore(), clock=ManualClock(), id_factory=SequentialIdFactory()
    )
    ref = BudgetAccountRef("c", BudgetLevel.CAMPAIGN, "c", BudgetType.MONEY)
    mgr.open_account(ref, limit=Decimal("1"), hardness=Hardness.HARD, currency="USD")
    return mgr, ref


def _req() -> LLMRequest:
    return LLMRequest(
        op_id="op-1", attempt_id="att-1", initiator="t", model_ref="m",
        messages=[{"role": "user", "content": "hi"}], max_tokens=64,
    )


def test_proxy_over_http_equivalent_to_local() -> None:
    fake = _FakeUnderlying()
    client = _http_client(create_gateway_app(fake))
    mgr, ref = _ledger()
    proxy = LLMProxy(client=client, ledger=mgr)
    res = proxy.request(
        _req(), account_ref=ref, reserve_amount=Decimal("0.05"), reservation_id="r1"
    )
    assert res.status is ProxyStatus.OK
    assert res.response is not None
    assert res.response.structured == {"kind": "FINISH"}
    assert fake.calls == 1  # ровно одна физическая отправка на шлюзе
    # учёт расхода — на стороне вызывающего (Decimal без потери точности по сети)
    assert mgr.consumed(ref) == Decimal("0.0125")


def test_gateway_error_maps_to_proxy_error() -> None:
    class _Boom:
        def complete(self, request: LLMRequest) -> LLMResponse:
            raise ConnectionError("провайдер недоступен")

    client = _http_client(create_gateway_app(_Boom()))
    mgr, ref = _ledger()
    proxy = LLMProxy(client=client, ledger=mgr)
    res = proxy.request(
        _req(), account_ref=ref, reserve_amount=Decimal("0.05"), reservation_id="r1"
    )
    assert res.status in (ProxyStatus.ERROR, ProxyStatus.UNKNOWN)
