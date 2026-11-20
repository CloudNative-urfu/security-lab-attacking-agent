"""Контракт capability budget-ledger."""

from __future__ import annotations

from decimal import Decimal

from attacking_agent.domain.budget import (
    BudgetAccountRef,
    InMemoryBudgetStore,
    LedgerManager,
    ReserveStatus,
    SettleStatus,
)
from attacking_agent.domain.common.clock import ManualClock
from attacking_agent.domain.config import BudgetLevel, BudgetType, Hardness


def _ref(budget_type: BudgetType = BudgetType.MONEY) -> BudgetAccountRef:
    return BudgetAccountRef(
        campaign_id="camp-1",
        level=BudgetLevel.CAMPAIGN,
        scope_key="camp-1",
        budget_type=budget_type,
    )


def _mgr(clock: ManualClock | None = None) -> LedgerManager:
    return LedgerManager(
        InMemoryBudgetStore(), clock=clock or ManualClock(), reservation_ttl_seconds=60
    )


def _open_money(mgr: LedgerManager, limit: str = "5.00", hard: bool = True) -> BudgetAccountRef:
    ref = _ref()
    mgr.open_account(
        ref,
        limit=Decimal(limit),
        currency="USD",
        hardness=Hardness.HARD if hard else Hardness.SOFT,
    )
    return ref


def test_decimal_precision_in_accounting() -> None:
    mgr = _mgr()
    ref = _open_money(mgr, "1.00")
    mgr.reserve(ref, Decimal("0.10"), "r1")
    mgr.start("r1", "a1")
    mgr.settle("a1", "c1", Decimal("0.10"))
    mgr.reserve(ref, Decimal("0.20"), "r2")
    mgr.start("r2", "a2")
    mgr.settle("a2", "c2", Decimal("0.20"))
    assert mgr.consumed(ref) == Decimal("0.30")  # не 0.30000000000000004


def test_reserve_reduces_available_and_over_reserve_rejected() -> None:
    mgr = _mgr()
    ref = _open_money(mgr, "5.00")
    r1 = mgr.reserve(ref, Decimal("3.00"), "r1")
    assert r1.status is ReserveStatus.OK
    assert mgr.available(ref) == Decimal("2.00")
    r2 = mgr.reserve(ref, Decimal("3.00"), "r2")
    assert r2.status is ReserveStatus.NO_BUDGET
    assert mgr.available(ref) == Decimal("2.00")  # неуспешный резерв не удержал


def test_hard_limit_cannot_be_exceeded() -> None:
    mgr = _mgr()
    ref = _open_money(mgr, "1.00")
    r = mgr.reserve(ref, Decimal("1.50"), "r1")
    assert r.status is ReserveStatus.NO_BUDGET


def test_idempotent_settle() -> None:
    mgr = _mgr()
    ref = _open_money(mgr, "5.00")
    mgr.reserve(ref, Decimal("2.00"), "r1")
    mgr.start("r1", "a1")
    s1 = mgr.settle("a1", "c1", Decimal("1.50"))
    s2 = mgr.settle("a1", "c1", Decimal("1.50"))
    assert s1.status is SettleStatus.SETTLED
    assert s2.status is SettleStatus.SETTLED
    assert mgr.consumed(ref) == Decimal("1.50")  # не удвоилось


def test_incompatible_settle_repeat_is_conflict() -> None:
    mgr = _mgr()
    ref = _open_money(mgr, "5.00")
    mgr.reserve(ref, Decimal("2.00"), "r1")
    mgr.start("r1", "a1")
    mgr.settle("a1", "c1", Decimal("1.50"))
    s = mgr.settle("a1", "c1", Decimal("2.00"))
    assert s.status is SettleStatus.CONFLICT
    assert mgr.consumed(ref) == Decimal("1.50")  # прежняя сумма не изменена


def test_cost_unknown_held_and_ttl_does_not_release() -> None:
    clock = ManualClock()
    mgr = _mgr(clock)
    ref = _open_money(mgr, "5.00")
    mgr.reserve(ref, Decimal("2.00"), "r1")
    mgr.start("r1", "a1")
    s = mgr.settle("a1", "c1", None)  # UNKNOWN
    assert s.status is SettleStatus.COST_UNKNOWN
    assert mgr.available(ref) == Decimal("3.00")  # удержано
    clock.advance(120)  # за пределами TTL
    mgr.sweep_expired()
    assert mgr.available(ref) == Decimal("3.00")  # TTL не освободил неизвестный расход


def test_release_only_on_not_sent() -> None:
    clock = ManualClock()
    mgr = _mgr(clock)
    ref = _open_money(mgr, "5.00")
    mgr.reserve(ref, Decimal("2.00"), "r1")
    assert mgr.available(ref) == Decimal("3.00")
    # попытка не отправлялась (start не вызывался) — надёжный NOT_SENT
    mgr.release_not_sent("r1")
    assert mgr.available(ref) == Decimal("5.00")


def test_started_reservation_not_released_by_sweep() -> None:
    clock = ManualClock()
    mgr = _mgr(clock)
    ref = _open_money(mgr, "5.00")
    mgr.reserve(ref, Decimal("2.00"), "r1")
    mgr.start("r1", "a1")  # отправка могла состояться
    clock.advance(120)
    mgr.sweep_expired()
    assert mgr.available(ref) == Decimal("3.00")  # не освобождаем начатую попытку


def test_bound_broken_on_actual_over_reserved_and_blocks_issuance() -> None:
    mgr = _mgr()
    ref = _open_money(mgr, "10.00")
    mgr.reserve(ref, Decimal("2.00"), "r1")
    mgr.start("r1", "a1")
    s = mgr.settle("a1", "c1", Decimal("3.00"))  # больше принятой верхней оценки
    assert s.status is SettleStatus.BOUND_BROKEN
    assert mgr.consumed(ref) == Decimal("3.00")  # фиксируем actual
    # выдача остановлена до разбора
    r = mgr.reserve(ref, Decimal("1.00"), "r2")
    assert r.status is ReserveStatus.INCIDENT


def test_reservation_id_reuse_across_accounts_denied() -> None:
    mgr = _mgr()
    ref_a = BudgetAccountRef("camp-1", BudgetLevel.CAMPAIGN, "a", BudgetType.MONEY)
    ref_b = BudgetAccountRef("camp-1", BudgetLevel.CAMPAIGN, "b", BudgetType.MONEY)
    mgr.open_account(ref_a, limit=Decimal("5"), hardness=Hardness.HARD, currency="USD")
    mgr.open_account(ref_b, limit=Decimal("5"), hardness=Hardness.HARD, currency="USD")
    assert mgr.reserve(ref_a, Decimal("2"), "shared-id").status is ReserveStatus.OK
    # тот же reservation_id для ДРУГОГО счёта не выдаёт бюджет
    assert mgr.reserve(ref_b, Decimal("2"), "shared-id").status is ReserveStatus.NO_BUDGET
    assert mgr.available(ref_b) == Decimal("5")  # остаток B не тронут
