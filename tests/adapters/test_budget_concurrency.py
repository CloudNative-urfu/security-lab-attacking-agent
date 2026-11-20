"""Состязательный тест: кросс-процессная сериализация бюджета (разные движки/соединения).

Моделирует двух независимых воркеров (каждый со своим Engine к одному файлу SQLite),
одновременно резервирующих сверх остатка. Корректная БД-блокировка не допускает превышения
жёсткого лимита — свойство, которого process-level threading.Lock не давал.
"""

from __future__ import annotations

import threading
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine

from attacking_agent.adapters.sql import SQLAlchemyBudgetStore, create_all
from attacking_agent.domain.budget import (
    BudgetAccountRef,
    LedgerManager,
    ReserveStatus,
)
from attacking_agent.domain.common.clock import ManualClock
from attacking_agent.domain.config import BudgetLevel, BudgetType, Hardness


def _ref() -> BudgetAccountRef:
    return BudgetAccountRef(
        campaign_id="camp-1",
        level=BudgetLevel.CAMPAIGN,
        scope_key="camp-1",
        budget_type=BudgetType.MONEY,
    )


def _worker(db_path: Path, amount: str, res_id: str, out: dict[str, ReserveStatus]) -> None:
    engine = create_engine(f"sqlite:///{db_path}")
    mgr = LedgerManager(SQLAlchemyBudgetStore(engine), clock=ManualClock())
    out[res_id] = mgr.reserve(_ref(), Decimal(amount), res_id).status
    engine.dispose()


def test_concurrent_workers_do_not_exceed_hard_limit(tmp_path: Path) -> None:
    db = tmp_path / "budget.db"
    setup_engine = create_engine(f"sqlite:///{db}")
    create_all(setup_engine)
    LedgerManager(SQLAlchemyBudgetStore(setup_engine), clock=ManualClock()).open_account(
        _ref(), limit=Decimal("3.00"), hardness=Hardness.HARD, currency="USD"
    )
    setup_engine.dispose()

    results: dict[str, ReserveStatus] = {}
    barrier = threading.Barrier(2)

    def run(res_id: str) -> None:
        barrier.wait()  # старт одновременно
        _worker(db, "2.00", res_id, results)

    t1 = threading.Thread(target=run, args=("r1",))
    t2 = threading.Thread(target=run, args=("r2",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    oks = [s for s in results.values() if s is ReserveStatus.OK]
    # два резерва по 2.00 при лимите 3.00 — суммарно 4.00 невозможно: ровно один проходит
    assert len(oks) == 1, results

    # итоговое состояние согласовано: подтверждён только один резерв
    check_engine = create_engine(f"sqlite:///{db}")
    mgr = LedgerManager(SQLAlchemyBudgetStore(check_engine), clock=ManualClock())
    assert mgr.available(_ref()) == Decimal("1.00")
    check_engine.dispose()
