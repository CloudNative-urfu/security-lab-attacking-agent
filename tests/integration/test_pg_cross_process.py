"""Доказательство кросс-процессных гарантий на реальном PostgreSQL.

Используются НАСТОЯЩИЕ отдельные процессы (multiprocessing, fork), каждый со своим
соединением к PG — это проверяет кросс-процессность (advisory locks), а не кросс-поточность.
"""

from __future__ import annotations

import multiprocessing as mp
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from attacking_agent.adapters.sql import (
    SQLAlchemyBudgetStore,
    SQLAlchemyMemoryStore,
    create_all,
)
from attacking_agent.domain.budget import BudgetAccountRef, LedgerManager
from attacking_agent.domain.common.clock import SystemClock
from attacking_agent.domain.common.ids import UuidIdFactory
from attacking_agent.domain.common.scope import Scope
from attacking_agent.domain.config import BudgetLevel, BudgetType, Hardness
from attacking_agent.domain.memory import KnowledgeInput, Writer

pytestmark = pytest.mark.integration
_CTX = mp.get_context("fork")  # отдельные ОС-процессы со своими соединениями


# --- бюджет: жёсткий лимит не превышается между процессами -----------------------


def _budget_ref(campaign: str) -> BudgetAccountRef:
    return BudgetAccountRef(campaign, BudgetLevel.CAMPAIGN, campaign, BudgetType.MONEY)


def _budget_worker(url: str, campaign: str, amount: str, res_id: str,
                   q: mp.Queue[str], barrier) -> None:  # type: ignore[no-untyped-def]
    engine = create_engine(url)
    mgr = LedgerManager(SQLAlchemyBudgetStore(engine), clock=SystemClock())
    barrier.wait()
    try:
        q.put(mgr.reserve(_budget_ref(campaign), Decimal(amount), res_id).status.value)
    finally:
        engine.dispose()


def test_pg_budget_hard_limit_cross_process(pg_url: str) -> None:
    campaign = f"camp-{uuid.uuid4().hex[:8]}"
    engine = create_engine(pg_url)
    create_all(engine)
    LedgerManager(SQLAlchemyBudgetStore(engine), clock=SystemClock()).open_account(
        _budget_ref(campaign), limit=Decimal("3.00"), hardness=Hardness.HARD, currency="USD"
    )
    engine.dispose()

    q: mp.Queue[str] = _CTX.Queue()
    barrier = _CTX.Barrier(2)
    procs = [
        _CTX.Process(
            target=_budget_worker,
            args=(pg_url, campaign, "2.00", f"{campaign}-r{i}", q, barrier),
        )
        for i in range(2)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(30)

    results = [q.get() for _ in range(2)]
    assert results.count("ok") == 1, results  # ровно один резерв 2.00 при лимите 3.00

    check = create_engine(pg_url)
    mgr = LedgerManager(SQLAlchemyBudgetStore(check), clock=SystemClock())
    assert mgr.available(_budget_ref(campaign)) == Decimal("1.00")
    check.dispose()


# --- память: branch-fence между процессами ---------------------------------------


def _mem_scope(campaign: str) -> Scope:
    return Scope(campaign_id=campaign, branch_id="b", target_ref="t")


def _mem_publish(url: str, campaign: str, base: int, statement: str,
                 q: mp.Queue[str] | None, barrier) -> None:  # type: ignore[no-untyped-def]
    engine = create_engine(url)
    store = SQLAlchemyMemoryStore(engine)
    writer = Writer(store, id_factory=UuidIdFactory())
    scope = _mem_scope(campaign)
    if barrier is not None:
        barrier.wait()
    cand = writer.prepare(scope, base_version=base, read_set=[],
                          knowledge=[KnowledgeInput(statement=statement)])
    res = writer.commit(scope, cand, writer.evaluate(scope, cand))
    if q is not None:
        q.put(res.status.value)
    engine.dispose()


def test_pg_memory_branch_fence_cross_process(pg_url: str) -> None:
    campaign = f"camp-{uuid.uuid4().hex[:8]}"
    engine = create_engine(pg_url)
    create_all(engine)
    engine.dispose()
    # засеять версию 1 в отдельном процессе
    p0 = _CTX.Process(target=_mem_publish, args=(pg_url, campaign, 0, "seed", None, None))
    p0.start()
    p0.join(30)

    q: mp.Queue[str] = _CTX.Queue()
    barrier = _CTX.Barrier(2)
    procs = [
        _CTX.Process(target=_mem_publish, args=(pg_url, campaign, 1, f"k{i}", q, barrier))
        for i in range(2)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(30)

    results = [q.get() for _ in range(2)]
    assert results.count("published") == 1, results  # ровно один под базой 1
    assert "version_conflict" in results, results

    check = create_engine(pg_url)
    store = SQLAlchemyMemoryStore(check)
    assert store.branch_version(_mem_scope(campaign)) == 2
    assert len(store.committed_knowledge(_mem_scope(campaign))) == 2  # seed + один
    check.dispose()


def test_pg_advisory_lock_is_transaction_scoped(pg_url: str) -> None:
    # Санити: advisory xact lock снимается при commit (не висит между транзакциями).
    engine = create_engine(pg_url)
    with engine.begin() as conn:
        conn.execute(text("select pg_advisory_xact_lock(999)"))
    with engine.connect() as conn:
        held = conn.execute(
            text("select count(*) from pg_locks where locktype='advisory' and objid=999")
        ).scalar()
    assert held == 0
    engine.dispose()
