"""Доказательство на реальном Temporal: retry/replay Activity не вызывает повторную
физическую отправку (durable журнал + атомарный claim диспетчера).

Сценарий: Workflow вызывает Activity, которая через диспетчер исполняет инструмент
(инкремент счётчика в PG). На ПЕРВОЙ попытке Activity искусственно падает ПОСЛЕ отправки.
Temporal ретраит Activity; на второй попытке диспетчер видит записанный результат по
(op_id, attempt_id) и НЕ отправляет повторно. Итог: физических отправок ровно одна.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

pytestmark = pytest.mark.integration

PG_URL = os.environ.get("AG_PG_URL", "postgresql+psycopg://ag:ag@localhost:5433/ag")
TEMPORAL = os.environ.get("AG_TEMPORAL", "localhost:7233")


def _temporal_reachable() -> bool:
    async def _ping() -> bool:
        try:
            await Client.connect(TEMPORAL, namespace="default")
            return True
        except Exception:
            return False

    try:
        return asyncio.run(asyncio.wait_for(_ping(), timeout=5))
    except Exception:
        return False


# --- Activity: исполняет инструмент через диспетчер, падает один раз после отправки ----


@activity.defn
def dispatch_activity(op_id: str) -> str:
    # Тяжёлые импорты отложены (вне Temporal workflow-sandbox).
    from decimal import Decimal

    from pydantic import BaseModel
    from sqlalchemy import create_engine, text

    from attacking_agent.adapters.sql import SQLAlchemyCallJournal, create_all
    from attacking_agent.domain.budget import (
        BudgetAccountRef,
        InMemoryBudgetStore,
        LedgerManager,
    )
    from attacking_agent.domain.common.clock import SystemClock
    from attacking_agent.domain.common.scope import Scope
    from attacking_agent.domain.config import (
        BudgetLevel,
        BudgetType,
        Hardness,
        PolicySpec,
    )
    from attacking_agent.domain.policy import PolicyEnforcer, StaticAddressResolver
    from attacking_agent.domain.tools import (
        Dispatcher,
        DispatchRequest,
        LocalToolRunner,
        ToolContext,
        ToolContract,
        ToolRegistry,
    )

    engine = create_engine(PG_URL)
    create_all(engine)

    class _P(BaseModel):
        op: str

    def counting_tool(ctx: ToolContext, params: _P) -> dict:
        # Реальная «физическая отправка»: инкремент счётчика в PG.
        with engine.begin() as conn:
            conn.execute(
                text(
                    "insert into t_send_counter(op_id, n) values (:o, 1) "
                    "on conflict (op_id) do update set n = t_send_counter.n + 1"
                ),
                {"o": params.op},
            )
        return {"sent": True}

    ledger = LedgerManager(InMemoryBudgetStore(), clock=SystemClock())
    acc = BudgetAccountRef("c", BudgetLevel.CAMPAIGN, "c", BudgetType.REQUESTS)
    ledger.open_account(acc, limit=Decimal("100"), hardness=Hardness.HARD)
    policy = PolicyEnforcer(
        PolicySpec(policy_version="p1", allowed_operations=["tool.send"], allowed_addresses=[]),
        resolver=StaticAddressResolver({}),
    )
    registry = ToolRegistry()
    registry.register(
        ToolContract(name="send", operation="tool.send", params_model=_P, executor=counting_tool)
    )
    dispatcher = Dispatcher(
        policy=policy, ledger=ledger, journal=SQLAlchemyCallJournal(engine),
        runner=LocalToolRunner(), registry=registry,
    )
    scope = Scope(campaign_id="c", branch_id="b", target_ref="t")
    req = DispatchRequest(
        op_id=op_id, attempt_id=op_id, tool_name="send", params={"op": op_id},
        scope=scope, account_ref=acc, reserve_amount=Decimal("1"),
        reservation_id=op_id,
    )
    status = dispatcher.dispatch(req).status.value
    engine.dispose()

    # Искусственный сбой ПОСЛЕ отправки на первой попытке -> Temporal ретраит Activity.
    if activity.info().attempt == 1:
        raise ApplicationError("искусственный сбой после отправки", type="Injected")
    return status


@workflow.defn
class IdempotencyWorkflow:
    @workflow.run
    async def run(self, op_id: str) -> str:
        return await workflow.execute_activity(
            dispatch_activity,
            op_id,
            start_to_close_timeout=timedelta(seconds=20),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(milliseconds=200), maximum_attempts=3
            ),
        )


def test_temporal_retry_does_not_resend() -> None:
    if not _temporal_reachable():
        pytest.skip(f"Temporal недоступен по {TEMPORAL} (docker compose up -d temporal)")

    op_id = f"op-{uuid.uuid4().hex[:10]}"
    task_queue = f"tq-{uuid.uuid4().hex[:8]}"

    async def run() -> int:
        from sqlalchemy import create_engine, text

        eng = create_engine(PG_URL)
        with eng.begin() as conn:
            conn.execute(
                text("create table if not exists t_send_counter (op_id text primary key, n int)")
            )
        client = await Client.connect(TEMPORAL, namespace="default")
        async with Worker(
            client,
            task_queue=task_queue,
            workflows=[IdempotencyWorkflow],
            activities=[dispatch_activity],
            activity_executor=ThreadPoolExecutor(max_workers=4),
        ):
            await client.execute_workflow(
                IdempotencyWorkflow.run,
                op_id,
                id=f"wf-{op_id}",
                task_queue=task_queue,
            )
        with eng.connect() as conn:
            n = conn.execute(
                text("select n from t_send_counter where op_id = :o"), {"o": op_id}
            ).scalar()
        eng.dispose()
        return int(n or 0)

    sends = asyncio.run(run())
    assert sends == 1, f"ожидалась ровно одна физическая отправка, получено {sends}"
