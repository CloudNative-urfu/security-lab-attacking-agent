"""SQLAlchemy-реализация порта :class:`BudgetStore` (источник истины о расходах).

``lock()`` открывает транзакцию (на уровне процесса сериализует доступ — для SQLite;
на PostgreSQL заменяется на ``SELECT FOR UPDATE`` / advisory locks). Все get/put внутри
одного ``lock()`` работают в одной транзакции и фиксируются при выходе.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, select
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


class SQLAlchemyBudgetStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session_factory = sessionmaker(engine)
        self._local = threading.local()
        self._guard = threading.Lock()

    @contextmanager
    def lock(self) -> Iterator[None]:
        if getattr(self._local, "session", None) is not None:
            yield
            return
        self._guard.acquire()
        session = self._session_factory()
        self._local.session = session
        try:
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

    Base.metadata.create_all(engine)
