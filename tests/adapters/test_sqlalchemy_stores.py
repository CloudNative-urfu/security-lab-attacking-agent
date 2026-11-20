"""Контракт SQLAlchemy-адаптеров: те же инварианты домена на реальном SQL-движке (SQLite).

Та же ORM используется на PostgreSQL в production (меняется только URL движка).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.pool import StaticPool

from attacking_agent.adapters.sql import (
    SQLAlchemyBudgetStore,
    SQLAlchemyCallJournal,
    create_all,
)
from attacking_agent.domain.budget import (
    BudgetAccountRef,
    LedgerManager,
    ReserveStatus,
    SettleStatus,
)
from attacking_agent.domain.common.clock import ManualClock
from attacking_agent.domain.common.ids import SequentialIdFactory
from attacking_agent.domain.config import BudgetLevel, BudgetType, Hardness
from attacking_agent.domain.tools import (
    IntentRecord,
    Observation,
    ObservationStatus,
    ResultRecord,
)


@pytest.fixture
def engine() -> Engine:
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_all(eng)
    return eng


def _ref() -> BudgetAccountRef:
    return BudgetAccountRef(
        campaign_id="camp-1",
        level=BudgetLevel.CAMPAIGN,
        scope_key="camp-1",
        budget_type=BudgetType.MONEY,
    )


def _mgr(engine: Engine) -> LedgerManager:
    return LedgerManager(
        SQLAlchemyBudgetStore(engine),
        clock=ManualClock(),
        id_factory=SequentialIdFactory(),
    )


def test_sql_budget_reserve_and_decimal_precision(engine: Engine) -> None:
    mgr = _mgr(engine)
    ref = _ref()
    mgr.open_account(ref, limit=Decimal("1.00"), hardness=Hardness.HARD, currency="USD")
    mgr.reserve(ref, Decimal("0.10"), "r1")
    mgr.start("r1", "a1")
    mgr.settle("a1", "c1", Decimal("0.10"))
    mgr.reserve(ref, Decimal("0.20"), "r2")
    mgr.start("r2", "a2")
    mgr.settle("a2", "c2", Decimal("0.20"))
    assert mgr.consumed(ref) == Decimal("0.30")  # точность сохранена в текстовом Decimal


def test_sql_budget_hard_limit_and_persistence(engine: Engine) -> None:
    mgr = _mgr(engine)
    ref = _ref()
    mgr.open_account(ref, limit=Decimal("1.00"), hardness=Hardness.HARD, currency="USD")
    assert mgr.reserve(ref, Decimal("1.50"), "r1").status is ReserveStatus.NO_BUDGET
    # новый менеджер поверх того же движка видит сохранённое состояние
    mgr2 = _mgr(engine)
    assert mgr2.available(ref) == Decimal("1.00")


def test_sql_budget_bound_broken(engine: Engine) -> None:
    mgr = _mgr(engine)
    ref = _ref()
    mgr.open_account(ref, limit=Decimal("10.00"), hardness=Hardness.HARD, currency="USD")
    mgr.reserve(ref, Decimal("2.00"), "r1")
    mgr.start("r1", "a1")
    s = mgr.settle("a1", "c1", Decimal("3.00"))
    assert s.status is SettleStatus.BOUND_BROKEN
    assert mgr.reserve(ref, Decimal("1.00"), "r2").status is ReserveStatus.INCIDENT


def test_sql_journal_roundtrip_and_unknowns(engine: Engine) -> None:
    journal = SQLAlchemyCallJournal(engine)
    journal.record_intent(IntentRecord("op-1", "att-1", "br-1", "http_post", "permit-1"))
    assert journal.has_intent("op-1", "att-1")
    journal.record_result(
        ResultRecord(
            op_id="op-1",
            attempt_id="att-1",
            branch_id="br-1",
            observation=Observation(ObservationStatus.UNKNOWN, {"k": "v"}),
            dispatch_status="unknown",
        )
    )
    assert len(journal.unresolved_unknowns("br-1")) == 1
    # идемпотентность записи результата
    journal.record_result(
        ResultRecord(
            op_id="op-1",
            attempt_id="att-1",
            branch_id="br-1",
            observation=Observation(ObservationStatus.OK, {}),
            dispatch_status="ok",
        )
    )
    got = journal.get_result("op-1", "att-1")
    assert got is not None
    assert got.observation.status is ObservationStatus.UNKNOWN  # первая запись не перезаписана
    journal.reconcile("op-1", "att-1", ObservationStatus.REFUSED)
    assert journal.unresolved_unknowns("br-1") == []


# --- SQLAlchemy MemoryStore: те же инварианты памяти на реальном движке ----------


def _mem_scope(branch: str = "br-1"):
    from attacking_agent.domain.common.scope import Scope

    return Scope(campaign_id="camp-1", branch_id=branch, target_ref="svc-demo")


def test_sql_memory_append_dedup_and_conflict(engine: Engine) -> None:
    from attacking_agent.adapters.sql import SQLAlchemyMemoryStore
    from attacking_agent.domain.memory import EventInput, Writer
    from attacking_agent.domain.memory.roles import AppendStatus

    writer = Writer(SQLAlchemyMemoryStore(engine))
    scope = _mem_scope()
    e = EventInput(event_id="ev-1", hash="h1", source="tool", kind="obs", payload={})
    assert writer.append_event(scope, e).status is AppendStatus.APPENDED
    assert writer.append_event(scope, e).status is AppendStatus.DUPLICATE
    e2 = EventInput(event_id="ev-1", hash="h2", source="tool", kind="obs", payload={})
    assert writer.append_event(scope, e2).status is AppendStatus.CONFLICT


def test_sql_memory_prepare_commit_read_and_isolation(engine: Engine) -> None:
    from attacking_agent.adapters.sql import SQLAlchemyMemoryStore
    from attacking_agent.domain.common.ids import UuidIdFactory
    from attacking_agent.domain.memory import (
        KnowledgeInput,
        Reader,
        ReadMode,
        Writer,
    )
    from attacking_agent.domain.memory.roles import CommitStatus

    store = SQLAlchemyMemoryStore(engine)
    writer = Writer(store, id_factory=UuidIdFactory())
    reader = Reader(store)
    scope = _mem_scope("br-1")
    base = store.branch_version(scope)
    cand = writer.prepare(
        scope,
        base_version=base,
        read_set=[],
        knowledge=[KnowledgeInput(statement="ввод отражается")],
    )
    assessment = writer.evaluate(scope, cand)
    r1 = writer.commit(scope, cand, assessment)
    assert r1.status is CommitStatus.PUBLISHED
    # повторный commit того же кандидата -> прежняя квитанция
    r2 = writer.commit(scope, cand, assessment)
    assert r2.receipt_id == r1.receipt_id
    # чтение видит знание в своей ветке
    pkg = reader.read(scope, ReadMode.TASK, query="отражается", limit=10)
    assert any("отражается" in k.statement for k in pkg.knowledge)
    # другая ветка не видит
    other = reader.read(_mem_scope("br-2"), ReadMode.ANALYSIS, query="отражается", limit=10)
    assert other.knowledge == []


def test_sql_memory_changed_composition_rejected(engine: Engine) -> None:
    from attacking_agent.adapters.sql import SQLAlchemyMemoryStore
    from attacking_agent.domain.common.ids import UuidIdFactory
    from attacking_agent.domain.memory import KnowledgeInput, Writer
    from attacking_agent.domain.memory.roles import CommitStatus

    store = SQLAlchemyMemoryStore(engine)
    writer = Writer(store, id_factory=UuidIdFactory())
    scope = _mem_scope()
    cand = writer.prepare(
        scope, base_version=0, read_set=[], knowledge=[KnowledgeInput(statement="k")]
    )
    assessment = writer.evaluate(scope, cand)
    tampered = cand.with_tampered_composition()
    r = writer.commit(scope, tampered, assessment)
    assert r.status in (CommitStatus.REJECTED, CommitStatus.STALE)
