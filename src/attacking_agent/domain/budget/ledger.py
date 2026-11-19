"""Менеджер бюджетов и устойчивый учёт (ledger).

Двухфазный допуск ресурса: ``reserve`` (удержание под lock) → ``start`` (permit на
одну попытку) → ``settle`` (фактический расход или UNKNOWN). Денежные величины —
Decimal. Жёсткий лимит превышать нельзя; неизвестная стоимость удерживается до
сверки и не освобождается по TTL; фактический перерасход фиксируется как BOUND_BROKEN
и останавливает выдачу.
"""

from __future__ import annotations

import threading
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from attacking_agent.domain.common.clock import Clock
from attacking_agent.domain.common.errors import BudgetError
from attacking_agent.domain.common.ids import IdFactory, UuidIdFactory
from attacking_agent.domain.config import BudgetLevel, BudgetType, Hardness


class ReservationState(StrEnum):
    HELD = "held"
    STARTED = "started"
    SETTLED = "settled"
    COST_UNKNOWN = "cost_unknown"
    RELEASED = "released"


class AccountStatus(StrEnum):
    OK = "ok"
    BOUND_BROKEN = "bound_broken"


class ReserveStatus(StrEnum):
    OK = "ok"
    NO_BUDGET = "no_budget"
    INCIDENT = "incident"  # счёт в инциденте (BOUND_BROKEN) — выдача остановлена


class StartStatus(StrEnum):
    OK = "ok"
    DENIED = "denied"
    NO_BUDGET = "no_budget"


class SettleStatus(StrEnum):
    SETTLED = "settled"
    COST_UNKNOWN = "cost_unknown"
    BOUND_BROKEN = "bound_broken"
    CONFLICT = "conflict"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class BudgetAccountRef:
    campaign_id: str
    level: BudgetLevel
    scope_key: str
    budget_type: BudgetType

    def key(self) -> str:
        return f"{self.campaign_id}:{self.level}:{self.scope_key}:{self.budget_type}"


@dataclass(slots=True)
class BudgetAccount:
    ref: BudgetAccountRef
    limit: Decimal
    hardness: Hardness
    currency: str | None = None
    consumed: Decimal = Decimal("0")
    status: AccountStatus = AccountStatus.OK


@dataclass(slots=True)
class Reservation:
    reservation_id: str
    account_key: str
    amount: Decimal
    state: ReservationState
    created_at: float
    attempt_id: str | None = None
    permit_id: str | None = None


@dataclass(slots=True)
class Charge:
    attempt_id: str
    charge_id: str
    actual: Decimal | None
    status: SettleStatus


@dataclass(frozen=True, slots=True)
class ReserveResult:
    status: ReserveStatus
    reservation_id: str | None = None
    soft_exceeded: bool = False


@dataclass(frozen=True, slots=True)
class StartResult:
    status: StartStatus
    permit_id: str | None = None


@dataclass(frozen=True, slots=True)
class SettleResult:
    status: SettleStatus
    charge_id: str | None = None


_HELD_STATES = (ReservationState.HELD, ReservationState.STARTED, ReservationState.COST_UNKNOWN)


class BudgetStore(Protocol):
    """Порт хранения счетов, резервов и расчётов."""

    def lock(self) -> AbstractContextManager[object]: ...
    def get_account(self, key: str) -> BudgetAccount | None: ...
    def put_account(self, account: BudgetAccount) -> None: ...
    def get_reservation(self, reservation_id: str) -> Reservation | None: ...
    def put_reservation(self, reservation: Reservation) -> None: ...
    def reservations_for_account(self, account_key: str) -> list[Reservation]: ...
    def reservation_by_attempt(self, attempt_id: str) -> Reservation | None: ...
    def get_charge(self, attempt_id: str, charge_id: str) -> Charge | None: ...
    def put_charge(self, charge: Charge) -> None: ...
    def all_reservations(self) -> list[Reservation]: ...


class InMemoryBudgetStore:
    """In-memory реализация :class:`BudgetStore` с RLock-семантикой транзакции."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._accounts: dict[str, BudgetAccount] = {}
        self._reservations: dict[str, Reservation] = {}
        self._charges: dict[tuple[str, str], Charge] = {}

    def lock(self) -> AbstractContextManager[object]:
        return self._lock

    def get_account(self, key: str) -> BudgetAccount | None:
        acc = self._accounts.get(key)
        return replace(acc) if acc is not None else None

    def put_account(self, account: BudgetAccount) -> None:
        self._accounts[account.ref.key()] = replace(account)

    def get_reservation(self, reservation_id: str) -> Reservation | None:
        r = self._reservations.get(reservation_id)
        return replace(r) if r is not None else None

    def put_reservation(self, reservation: Reservation) -> None:
        self._reservations[reservation.reservation_id] = replace(reservation)

    def reservations_for_account(self, account_key: str) -> list[Reservation]:
        return [replace(r) for r in self._reservations.values() if r.account_key == account_key]

    def reservation_by_attempt(self, attempt_id: str) -> Reservation | None:
        for r in self._reservations.values():
            if r.attempt_id == attempt_id:
                return replace(r)
        return None

    def get_charge(self, attempt_id: str, charge_id: str) -> Charge | None:
        c = self._charges.get((attempt_id, charge_id))
        return replace(c) if c is not None else None

    def put_charge(self, charge: Charge) -> None:
        self._charges[(charge.attempt_id, charge.charge_id)] = replace(charge)

    def all_reservations(self) -> list[Reservation]:
        return [replace(r) for r in self._reservations.values()]


class LedgerManager:
    """Доменный менеджер бюджетов поверх :class:`BudgetStore`."""

    def __init__(
        self,
        store: BudgetStore,
        clock: Clock,
        reservation_ttl_seconds: float = 300.0,
        id_factory: IdFactory | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._ttl = reservation_ttl_seconds
        self._ids = id_factory or UuidIdFactory()

    # --- управление счётом ---------------------------------------------------

    def open_account(
        self,
        ref: BudgetAccountRef,
        limit: Decimal,
        hardness: Hardness,
        currency: str | None = None,
    ) -> None:
        with self._store.lock():
            if self._store.get_account(ref.key()) is not None:
                raise BudgetError(f"счёт {ref.key()} уже открыт")
            self._store.put_account(
                BudgetAccount(ref=ref, limit=limit, hardness=hardness, currency=currency)
            )

    def _account(self, ref: BudgetAccountRef) -> BudgetAccount:
        acc = self._store.get_account(ref.key())
        if acc is None:
            raise BudgetError(f"счёт {ref.key()} не открыт")
        return acc

    def _held_total(self, account_key: str) -> Decimal:
        total = Decimal("0")
        for r in self._store.reservations_for_account(account_key):
            if r.state in _HELD_STATES:
                total += r.amount
        return total

    def available(self, ref: BudgetAccountRef) -> Decimal:
        with self._store.lock():
            acc = self._account(ref)
            return acc.limit - acc.consumed - self._held_total(ref.key())

    def consumed(self, ref: BudgetAccountRef) -> Decimal:
        with self._store.lock():
            return self._account(ref).consumed

    # --- двухфазный допуск ---------------------------------------------------

    def reserve(
        self, ref: BudgetAccountRef, amount: Decimal, reservation_id: str
    ) -> ReserveResult:
        if amount < Decimal("0"):
            raise BudgetError("сумма резерва не может быть отрицательной")
        with self._store.lock():
            acc = self._account(ref)
            if acc.status is AccountStatus.BOUND_BROKEN:
                return ReserveResult(ReserveStatus.INCIDENT)
            existing = self._store.get_reservation(reservation_id)
            if existing is not None:
                return ReserveResult(ReserveStatus.OK, reservation_id)
            available = acc.limit - acc.consumed - self._held_total(ref.key())
            soft_exceeded = False
            if amount > available:
                if acc.hardness is Hardness.HARD:
                    return ReserveResult(ReserveStatus.NO_BUDGET)
                soft_exceeded = True
            self._store.put_reservation(
                Reservation(
                    reservation_id=reservation_id,
                    account_key=ref.key(),
                    amount=amount,
                    state=ReservationState.HELD,
                    created_at=self._clock.now().timestamp(),
                )
            )
            return ReserveResult(ReserveStatus.OK, reservation_id, soft_exceeded)

    def start(self, reservation_id: str, attempt_id: str) -> StartResult:
        with self._store.lock():
            res = self._store.get_reservation(reservation_id)
            if res is None:
                return StartResult(StartStatus.DENIED)
            acc = self._store.get_account(res.account_key)
            if acc is not None and acc.status is AccountStatus.BOUND_BROKEN:
                return StartResult(StartStatus.NO_BUDGET)
            if res.state is ReservationState.STARTED:
                if res.attempt_id == attempt_id:
                    return StartResult(StartStatus.OK, res.permit_id)
                return StartResult(StartStatus.DENIED)
            if res.state is not ReservationState.HELD:
                return StartResult(StartStatus.DENIED)
            permit_id = self._ids.new_id("permit")
            res.state = ReservationState.STARTED
            res.attempt_id = attempt_id
            res.permit_id = permit_id
            self._store.put_reservation(res)
            return StartResult(StartStatus.OK, permit_id)

    def settle(
        self, attempt_id: str, charge_id: str, actual: Decimal | None
    ) -> SettleResult:
        with self._store.lock():
            existing = self._store.get_charge(attempt_id, charge_id)
            if existing is not None:
                if existing.actual == actual:
                    return SettleResult(existing.status, charge_id)
                return SettleResult(SettleStatus.CONFLICT, charge_id)
            res = self._store.reservation_by_attempt(attempt_id)
            if res is None:
                return SettleResult(SettleStatus.NOT_FOUND)
            if actual is None:
                res.state = ReservationState.COST_UNKNOWN
                self._store.put_reservation(res)
                self._store.put_charge(
                    Charge(attempt_id, charge_id, None, SettleStatus.COST_UNKNOWN)
                )
                return SettleResult(SettleStatus.COST_UNKNOWN, charge_id)
            acc = self._store.get_account(res.account_key)
            if acc is None:
                raise BudgetError(f"счёт {res.account_key} не открыт")
            acc.consumed += actual
            res.state = ReservationState.SETTLED
            status = SettleStatus.SETTLED
            if actual > res.amount:
                acc.status = AccountStatus.BOUND_BROKEN
                status = SettleStatus.BOUND_BROKEN
            self._store.put_reservation(res)
            self._store.put_account(acc)
            self._store.put_charge(Charge(attempt_id, charge_id, actual, status))
            return SettleResult(status, charge_id)

    def release_not_sent(self, reservation_id: str) -> None:
        with self._store.lock():
            res = self._store.get_reservation(reservation_id)
            if res is None:
                raise BudgetError(f"резерв {reservation_id} не найден")
            if res.state is not ReservationState.HELD:
                raise BudgetError(
                    "освободить резерв как неиспользованный можно только при доказанном "
                    f"NOT_SENT (состояние {res.state})"
                )
            res.state = ReservationState.RELEASED
            self._store.put_reservation(res)

    def sweep_expired(self) -> list[str]:
        """Освободить просроченные HELD-резервы (надёжный NOT_SENT). Вернуть их id."""
        released: list[str] = []
        now = self._clock.now().timestamp()
        with self._store.lock():
            for res in self._store.all_reservations():
                if res.state is ReservationState.HELD and now - res.created_at > self._ttl:
                    res.state = ReservationState.RELEASED
                    self._store.put_reservation(res)
                    released.append(res.reservation_id)
        return released
