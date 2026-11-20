"""Состязательный тест: атомарное застолбление попытки в диспетчере.

Два конкурентных dispatch с одинаковыми (op_id, attempt_id) должны привести ровно к
одной физической отправке — даже если оба прошли проверку «ещё нет результата».
"""

from __future__ import annotations

import threading
import time
from decimal import Decimal

from pydantic import BaseModel

from attacking_agent.domain.budget import (
    BudgetAccountRef,
    InMemoryBudgetStore,
    LedgerManager,
)
from attacking_agent.domain.common.clock import ManualClock
from attacking_agent.domain.common.ids import SequentialIdFactory
from attacking_agent.domain.common.scope import Scope
from attacking_agent.domain.config import BudgetLevel, BudgetType, Hardness, PolicySpec
from attacking_agent.domain.policy import PolicyEnforcer, StaticAddressResolver
from attacking_agent.domain.tools import (
    Dispatcher,
    DispatchRequest,
    InMemoryCallJournal,
    LocalToolRunner,
    ToolContext,
    ToolContract,
    ToolRegistry,
)


class _P(BaseModel):
    url: str


def test_concurrent_same_attempt_runs_tool_once() -> None:
    runs: list[str] = []
    started = threading.Barrier(2)

    def slow_tool(ctx: ToolContext, params: _P) -> dict:
        time.sleep(0.05)  # окно для гонки
        runs.append(params.url)
        return {"status_code": 200}

    store = InMemoryBudgetStore()
    mgr = LedgerManager(store, clock=ManualClock(), id_factory=SequentialIdFactory())
    acc = BudgetAccountRef("c", BudgetLevel.CAMPAIGN, "c", BudgetType.REQUESTS)
    mgr.open_account(acc, limit=Decimal("10"), hardness=Hardness.HARD)
    policy = PolicyEnforcer(
        PolicySpec(policy_version="p1", allowed_operations=["http.post"],
                   allowed_addresses=["https://demo.svc.internal/*"]),
        resolver=StaticAddressResolver({"demo.svc.internal": ["203.0.113.10"]}),
    )
    journal = InMemoryCallJournal()
    registry = ToolRegistry()
    registry.register(
        ToolContract(name="t", operation="http.post", params_model=_P,
                     executor=slow_tool, address_field="url")
    )
    dispatcher = Dispatcher(policy=policy, ledger=mgr, journal=journal,
                            runner=LocalToolRunner(), registry=registry)

    scope = Scope(campaign_id="c", branch_id="b", target_ref="t")
    req = DispatchRequest(
        op_id="op-1", attempt_id="att-1", tool_name="t",
        params={"url": "https://demo.svc.internal/x"}, scope=scope,
        account_ref=acc, reserve_amount=Decimal("1"), reservation_id="res-1",
    )

    def go() -> None:
        started.wait()
        dispatcher.dispatch(req)

    t1 = threading.Thread(target=go)
    t2 = threading.Thread(target=go)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(runs) == 1  # одна физическая отправка, несмотря на гонку
