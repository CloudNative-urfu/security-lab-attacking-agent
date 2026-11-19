"""Контракт capability tool-dispatch и журнала вызовов."""

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
from attacking_agent.domain.common.scope import Scope
from attacking_agent.domain.config import BudgetLevel, BudgetType, Hardness, PolicySpec
from attacking_agent.domain.policy import PolicyEnforcer, StaticAddressResolver
from attacking_agent.domain.tools import (
    Dispatcher,
    DispatchRequest,
    DispatchStatus,
    InMemoryCallJournal,
    LocalToolRunner,
    ObservationStatus,
    ToolContext,
    ToolContract,
    ToolRegistry,
)


class _Params(BaseModel):
    url: str
    body: str = ""


def _scope() -> Scope:
    return Scope(campaign_id="camp-1", branch_id="br-1", target_ref="svc-demo")


def _account(mgr: LedgerManager) -> BudgetAccountRef:
    ref = BudgetAccountRef(
        campaign_id="camp-1",
        level=BudgetLevel.CAMPAIGN,
        scope_key="camp-1",
        budget_type=BudgetType.REQUESTS,
    )
    mgr.open_account(ref, limit=Decimal("10"), hardness=Hardness.HARD)
    return ref


class _Harness:
    """Сборка диспетчера с подключаемыми компонентами для тестов."""

    def __init__(self, tool_fn=None, cap: int = 5) -> None:
        self.calls: list[str] = []

        def default_fn(ctx: ToolContext, params: _Params) -> dict:
            self.calls.append(params.url)
            return {"status_code": 200, "echo": params.body}

        self.tool_fn = tool_fn or default_fn
        self.store = InMemoryBudgetStore()
        self.mgr = LedgerManager(
            self.store, clock=ManualClock(), id_factory=SequentialIdFactory()
        )
        self.account = _account(self.mgr)
        self.policy = PolicyEnforcer(
            PolicySpec(
                policy_version="p1",
                allowed_operations=["http.post"],
                allowed_addresses=["https://demo.svc.internal/*"],
            ),
            resolver=StaticAddressResolver({"demo.svc.internal": ["203.0.113.10"]}),
        )
        self.journal = InMemoryCallJournal()
        self.runner = LocalToolRunner(subrequest_cap=cap)
        self.registry = ToolRegistry()
        self.registry.register(
            ToolContract(
                name="http_post",
                operation="http.post",
                params_model=_Params,
                executor=self.tool_fn,
                address_field="url",
            )
        )
        self.dispatcher = Dispatcher(
            policy=self.policy,
            ledger=self.mgr,
            journal=self.journal,
            runner=self.runner,
            registry=self.registry,
        )

    def req(self, op_id="op-1", attempt_id="att-1", url="https://demo.svc.internal/feedback"):
        return DispatchRequest(
            op_id=op_id,
            attempt_id=attempt_id,
            tool_name="http_post",
            params={"url": url, "body": "hi"},
            scope=_scope(),
            account_ref=self.account,
            reserve_amount=Decimal("1"),
            reservation_id=f"res-{attempt_id}",
        )


def test_invalid_params_rejected_before_send() -> None:
    h = _Harness()
    req = h.req()
    req = req.model_copy(update={"params": {"body": "no url field"}})
    res = h.dispatcher.dispatch(req)
    assert res.status is DispatchStatus.INVALID
    assert h.calls == []  # отправки не было


def test_operation_outside_allowlist_denied() -> None:
    h = _Harness()
    # контракт с запрещённой операцией
    h.registry.register(
        ToolContract(
            name="http_delete",
            operation="http.delete",
            params_model=_Params,
            executor=h.tool_fn,
            address_field="url",
        )
    )
    req = h.req().model_copy(update={"tool_name": "http_delete"})
    res = h.dispatcher.dispatch(req)
    assert res.status is DispatchStatus.DENIED
    assert h.calls == []


def test_address_outside_allowlist_denied() -> None:
    h = _Harness()
    req = h.req(url="https://evil.example/collect")
    res = h.dispatcher.dispatch(req)
    assert res.status is DispatchStatus.DENIED
    assert h.calls == []


def test_intent_recorded_before_result() -> None:
    h = _Harness()
    res = h.dispatcher.dispatch(h.req())
    assert res.status is DispatchStatus.OK
    assert h.journal.has_intent("op-1", "att-1")
    assert h.journal.get_result("op-1", "att-1") is not None


def test_result_recorded_even_without_knowledge() -> None:
    def noisy(ctx: ToolContext, params: _Params) -> dict:
        return {}  # пустой результат, знание не извлекается

    h = _Harness(tool_fn=noisy)
    res = h.dispatcher.dispatch(h.req())
    assert res.status is DispatchStatus.OK
    assert h.journal.get_result("op-1", "att-1") is not None


def test_idempotent_replay_runs_runner_once() -> None:
    h = _Harness()
    h.dispatcher.dispatch(h.req())
    h.dispatcher.dispatch(h.req())  # тот же op/attempt
    assert len(h.calls) == 1  # повторная доставка не вызывает вторую отправку


def test_harness_retry_uses_new_attempt() -> None:
    h = _Harness()
    h.dispatcher.dispatch(h.req(attempt_id="att-1"))
    h.dispatcher.dispatch(h.req(op_id="op-1", attempt_id="att-2"))
    assert len(h.calls) == 2  # новая попытка — новый permit и новая отправка


def test_unknown_blocks_dependent_actions() -> None:
    def ambiguous(ctx: ToolContext, params: _Params) -> dict:
        raise TimeoutError("ответ не получен")

    h = _Harness(tool_fn=ambiguous)
    res = h.dispatcher.dispatch(h.req())
    assert res.status is DispatchStatus.UNKNOWN
    assert h.dispatcher.has_unresolved_unknown(_scope()) is True
    # после сверки исхода блокировка снимается
    h.dispatcher.reconcile("op-1", "att-1", ObservationStatus.REFUSED)
    assert h.dispatcher.has_unresolved_unknown(_scope()) is False


def test_subrequest_cap_enforced() -> None:
    def greedy(ctx: ToolContext, params: _Params) -> dict:
        for _ in range(10):
            ctx.subrequest()
        return {"ok": True}

    h = _Harness(tool_fn=greedy, cap=3)
    res = h.dispatcher.dispatch(h.req())
    assert res.status is DispatchStatus.ERROR
    assert res.observation is not None
    assert "cap" in (res.observation.error or "").lower() or res.observation.subrequests <= 3


def test_runner_context_has_no_trusted_secrets() -> None:
    captured: dict = {}

    def grab(ctx: ToolContext, params: _Params) -> dict:
        captured["ctx"] = ctx
        return {}

    h = _Harness(tool_fn=grab)
    h.dispatcher.dispatch(h.req())
    ctx = captured["ctx"]
    for forbidden in ("secrets", "db", "model_key", "credentials", "api_key"):
        assert not hasattr(ctx, forbidden)


def test_new_attempt_after_unknown_requires_new_permit() -> None:
    # Нельзя переиспользовать резерв стартованной попытки под другой attempt_id.
    h = _Harness()
    h.mgr.reserve(h.account, Decimal("1"), "res-x")
    assert h.mgr.start("res-x", "attA").status.value == "ok"
    second = h.mgr.start("res-x", "attB")
    assert second.status.value == "denied"
