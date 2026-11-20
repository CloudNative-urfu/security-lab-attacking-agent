"""SQLAlchemy-реализация порта :class:`BudgetStore` (источник истины о расходах).

``lock()`` открывает транзакцию с **реальной БД-блокировкой**, удерживающей инвариант
жёсткого лимита между независимыми процессами (воркерами):

- SQLite: ``BEGIN IMMEDIATE`` (RESERVED-лок на файл сразу при старте транзакции) +
  ``busy_timeout`` — конкурентные записи сериализуются, а не ошибаются.
- PostgreSQL: transaction-level advisory lock (``pg_advisory_xact_lock``), снимается при
  commit/rollback.

Все get/put внутри одного ``lock()`` работают в одной сериализованной транзакции.
"""

from __future__ import annotations

import threading
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker

from attacking_agent.domain.budget import (
    AccountStatus,
    BudgetAccount,
    BudgetAccountRef,
    Charge,
    Reservation,
    ReservationState,
    SettleStatus,
)
from attacking_agent.domain.config import BudgetLevel, BudgetType, Hardness

from .models import BudgetAccountRow, ChargeRow, ReservationRow

# Константный ключ advisory-лока бюджета для PostgreSQL (сериализация записей бюджета).
_PG_BUDGET_LOCK_KEY = 0x42_55_44_47  # "BUDG"


def _to_account(row: BudgetAccountRow) -> BudgetAccount:
    return BudgetAccount(
        ref=BudgetAccountRef(
            campaign_id=row.campaign_id,
            level=BudgetLevel(row.level),
            scope_key=row.scope_key,
            budget_type=BudgetType(row.budget_type),
        ),
        limit=row.limit_amount,
        hardness=Hardness(row.hardness),
        currency=row.currency,
        consumed=row.consumed,
        status=AccountStatus(row.status),
    )


def _to_reservation(row: ReservationRow) -> Reservation:
    return Reservation(
        reservation_id=row.reservation_id,
        account_key=row.account_key,
        amount=row.amount,
        state=ReservationState(row.state),
        created_at=row.created_at,
        attempt_id=row.attempt_id,
        permit_id=row.permit_id,
    )


def _to_charge(row: ChargeRow) -> Charge:
    return Charge(
        attempt_id=row.attempt_id,
        charge_id=row.charge_id,
        actual=row.actual,
        status=SettleStatus(row.status),
    )


_immediate_engines: weakref.WeakSet[Engine] = weakref.WeakSet()


def configure_sqlite_engine(engine: Engine, busy_timeout_ms: int = 10000) -> None:
    """Настроить SQLite на BEGIN IMMEDIATE + busy_timeout для кросс-процессной сериализации.

    Вызывать **до первого соединения** (из ``create_all`` или при создании стора).
    Идемпотентно по движку; без ``dispose`` (не разрушает in-memory БД).
    """
    if engine.dialect.name != "sqlite" or engine in _immediate_engines:
        return
    _immediate_engines.add(engine)

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: Any, _rec: Any) -> None:
        # Отключаем неявный BEGIN pysqlite, чтобы эмитить свой BEGIN IMMEDIATE.
        dbapi_conn.isolation_level = None
        cur = dbapi_conn.cursor()
        cur.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        cur.close()

    @event.listens_for(engine, "begin")
    def _on_begin(conn: Any) -> None:
        conn.exec_driver_sql("BEGIN IMMEDIATE")


class SQLAlchemyBudgetStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._dialect = engine.dialect.name
        self._session_factory = sessionmaker(engine)
        self._local = threading.local()
        self._guard = threading.Lock()
        configure_sqlite_engine(engine)

    def _acquire_db_lock(self, session: Session) -> None:
        # Кросс-процессная сериализация записей бюджета.
        if self._dialect == "postgresql":
            session.execute(
                text("SELECT pg_advisory_xact_lock(:k)"), {"k": _PG_BUDGET_LOCK_KEY}
            )
        elif self._dialect == "sqlite":
            # BEGIN IMMEDIATE уже взял RESERVED-лок; форсируем старт транзакции.
            session.execute(text("SELECT 1"))

    @contextmanager
    def lock(self) -> Iterator[None]:
        if getattr(self._local, "session", None) is not None:
            yield
            return
        self._guard.acquire()
        session = self._session_factory()
        self._local.session = session
        try:
            self._acquire_db_lock(session)
            yield
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()
            self._local.session = None
            self._guard.release()

    def _session(self) -> Session:
        session: Session | None = getattr(self._local, "session", None)
        if session is None:
            raise RuntimeError("операция BudgetStore должна выполняться внутри lock()")
        return session

    # --- порт BudgetStore ---------------------------------------------------

    def get_account(self, key: str) -> BudgetAccount | None:
        row = self._session().get(BudgetAccountRow, key)
        return _to_account(row) if row is not None else None

    def put_account(self, account: BudgetAccount) -> None:
        session = self._session()
        key = account.ref.key()
        row = session.get(BudgetAccountRow, key)
        if row is None:
            row = BudgetAccountRow(account_key=key)
            session.add(row)
        row.campaign_id = account.ref.campaign_id
        row.level = account.ref.level.value
        row.scope_key = account.ref.scope_key
        row.budget_type = account.ref.budget_type.value
        row.limit_amount = account.limit
        row.hardness = account.hardness.value
        row.currency = account.currency
        row.consumed = account.consumed
        row.status = account.status.value
        session.flush()

    def get_reservation(self, reservation_id: str) -> Reservation | None:
        row = self._session().get(ReservationRow, reservation_id)
        return _to_reservation(row) if row is not None else None

    def put_reservation(self, reservation: Reservation) -> None:
        session = self._session()
        row = session.get(ReservationRow, reservation.reservation_id)
        if row is None:
            row = ReservationRow(reservation_id=reservation.reservation_id)
            session.add(row)
        row.account_key = reservation.account_key
        row.amount = reservation.amount
        row.state = reservation.state.value
        row.created_at = reservation.created_at
        row.attempt_id = reservation.attempt_id
        row.permit_id = reservation.permit_id
        session.flush()

    def reservations_for_account(self, account_key: str) -> list[Reservation]:
        rows = self._session().scalars(
            select(ReservationRow).where(ReservationRow.account_key == account_key)
        )
        return [_to_reservation(r) for r in rows]

    def reservation_by_attempt(self, attempt_id: str) -> Reservation | None:
        row = self._session().scalars(
            select(ReservationRow).where(ReservationRow.attempt_id == attempt_id)
        ).first()
        return _to_reservation(row) if row is not None else None

    def get_charge(self, attempt_id: str, charge_id: str) -> Charge | None:
        row = self._session().get(ChargeRow, (attempt_id, charge_id))
        return _to_charge(row) if row is not None else None

    def put_charge(self, charge: Charge) -> None:
        session = self._session()
        row = session.get(ChargeRow, (charge.attempt_id, charge.charge_id))
        if row is None:
            row = ChargeRow(attempt_id=charge.attempt_id, charge_id=charge.charge_id)
            session.add(row)
        row.actual = charge.actual
        row.status = charge.status.value
        session.flush()

    def all_reservations(self) -> list[Reservation]:
        rows = self._session().scalars(select(ReservationRow))
        return [_to_reservation(r) for r in rows]


def create_all(engine: Engine) -> None:
    """Создать схему (для MVP/тестов; в production — миграции)."""
    from .models import Base

    configure_sqlite_engine(engine)
    Base.metadata.create_all(engine)
