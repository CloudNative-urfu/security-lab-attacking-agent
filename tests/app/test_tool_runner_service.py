"""Контракт: диспетчер исполняет инструмент через сетевой HttpToolRunner; allowlist анти-RCE."""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient
from services.tool_runner.app import create_runner_app

from attacking_agent.adapters.http_tool_runner import HttpToolRunner
from attacking_agent.adapters.sample_tools import EchoParams, echo_tool
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
    DispatchStatus,
    InMemoryCallJournal,
    ObservationStatus,
    RunRequest,
    ToolContract,
    ToolRegistry,
)

_ECHO_ID = ("attacking_agent.adapters.sample_tools", "echo_tool")


def _http_runner(allowlist) -> HttpToolRunner:
    app = create_runner_app(allowlist=allowlist)
    return HttpToolRunner("http://runner", client=TestClient(app))


def _dispatcher(runner) -> tuple[Dispatcher, BudgetAccountRef, Scope]:
    mgr = LedgerManager(
        InMemoryBudgetStore(), clock=ManualClock(), id_factory=SequentialIdFactory()
    )
    acc = BudgetAccountRef("c", BudgetLevel.CAMPAIGN, "c", BudgetType.REQUESTS)
    mgr.open_account(acc, limit=Decimal("10"), hardness=Hardness.HARD)
    policy = PolicyEnforcer(
        PolicySpec(policy_version="p1", allowed_operations=["echo"], allowed_addresses=[]),
        resolver=StaticAddressResolver({}),
    )
    registry = ToolRegistry()
    registry.register(
        ToolContract(name="echo", operation="echo", params_model=EchoParams, executor=echo_tool)
    )
    dispatcher = Dispatcher(
        policy=policy, ledger=mgr, journal=InMemoryCallJournal(), runner=runner, registry=registry
    )
    return dispatcher, acc, Scope(campaign_id="c", branch_id="b", target_ref="t")


def test_dispatch_through_remote_runner_equivalent() -> None:
    runner = _http_runner({_ECHO_ID})
    dispatcher, acc, scope = _dispatcher(runner)
    res = dispatcher.dispatch(
        DispatchRequest(
            op_id="op-1", attempt_id="att-1", tool_name="echo", params={"body": "probe"},
            scope=scope, account_ref=acc, reserve_amount=Decimal("1"), reservation_id="r1",
        )
    )
    assert res.status is DispatchStatus.OK
    assert res.observation is not None
    assert res.observation.payload["reflected"] == "probe"
    assert res.observation.subrequests == 1  # cap-учёт сохранён на стороне runner


def test_tool_outside_allowlist_rejected() -> None:
    # echo НЕ в allowlist -> сервис отклоняет (403) -> наблюдение ERROR, инструмент не исполнен
    runner = _http_runner(set())
    obs = runner.run(
        RunRequest(executor=echo_tool, params=EchoParams(body="x"), address=None)
    )
    assert obs.status is ObservationStatus.ERROR
    assert "allowlist" in (obs.error or "")
