"""Durable журнал попыток LLM Proxy: повтор попытки не вызывает провайдера повторно."""

from __future__ import annotations

from decimal import Decimal

from attacking_agent.domain.budget import (
    BudgetAccountRef,
    InMemoryBudgetStore,
    LedgerManager,
)
from attacking_agent.domain.common.clock import ManualClock
from attacking_agent.domain.common.ids import SequentialIdFactory
from attacking_agent.domain.config import BudgetLevel, BudgetType, Hardness
from attacking_agent.domain.llm import (
    InMemoryLLMJournal,
    LLMProxy,
    LLMRequest,
    LLMResponse,
    ProxyStatus,
    Usage,
)


class _CountingClient:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            content="ok",
            structured={"k": "v"},
            usage=Usage(prompt_tokens=10, completion_tokens=5),
            cost=Decimal("0.003"),
        )


def _mgr() -> tuple[LedgerManager, BudgetAccountRef]:
    mgr = LedgerManager(
        InMemoryBudgetStore(), clock=ManualClock(), id_factory=SequentialIdFactory()
    )
    ref = BudgetAccountRef("c", BudgetLevel.CAMPAIGN, "c", BudgetType.MONEY)
    mgr.open_account(ref, limit=Decimal("1"), hardness=Hardness.HARD, currency="USD")
    return mgr, ref


def _req(attempt_id: str = "att-1") -> LLMRequest:
    return LLMRequest(
        op_id="op-1", attempt_id=attempt_id, initiator="t", model_ref="m",
        messages=[{"role": "user", "content": "hi"}], max_tokens=32,
    )


def test_replay_same_attempt_does_not_call_provider_again() -> None:
    mgr, ref = _mgr()
    client = _CountingClient()
    proxy = LLMProxy(client=client, ledger=mgr, journal=InMemoryLLMJournal())
    amt = Decimal("0.05")
    r1 = proxy.request(_req(), account_ref=ref, reserve_amount=amt, reservation_id="res-1")
    r2 = proxy.request(_req(), account_ref=ref, reserve_amount=amt, reservation_id="res-1")
    assert r1.status is ProxyStatus.OK
    assert r2.status is ProxyStatus.OK
    assert client.calls == 1  # повтор попытки не вызвал провайдера второй раз
    assert mgr.consumed(ref) == Decimal("0.003")  # и расход не удвоился


def test_distinct_attempts_still_call_provider() -> None:
    mgr, ref = _mgr()
    client = _CountingClient()
    proxy = LLMProxy(client=client, ledger=mgr, journal=InMemoryLLMJournal())
    amt = Decimal("0.05")
    proxy.request(_req("att-1"), account_ref=ref, reserve_amount=amt, reservation_id="res-1")
    proxy.request(_req("att-2"), account_ref=ref, reserve_amount=amt, reservation_id="res-2")
    assert client.calls == 2
