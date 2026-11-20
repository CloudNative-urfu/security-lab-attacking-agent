"""Контракт capability strategy-library."""

from __future__ import annotations

from attacking_agent.domain.common.ids import SequentialIdFactory
from attacking_agent.domain.common.scope import Scope
from attacking_agent.domain.strategy import (
    InMemoryStrategyStore,
    StrategyLibrary,
    StrategySpec,
)


def _scope(branch: str = "br-1") -> Scope:
    return Scope(campaign_id="c", branch_id=branch, target_ref="t")


def _lib() -> StrategyLibrary:
    return StrategyLibrary(InMemoryStrategyStore(), id_factory=SequentialIdFactory())


def _spec(technique: str, effect: str = "", applic: str = "") -> StrategySpec:
    return StrategySpec(
        technique=technique,
        preconditions=[],
        expected_effect=effect,
        applicability=applic,
    )


def test_ranked_scoped_retrieval() -> None:
    lib = _lib()
    scope = _scope()
    lib.record(scope, _spec("инъекция в поле ввода формы", "отражение ввода"))
    lib.record(scope, _spec("перебор идентификаторов заказа", "доступ к чужим данным"))
    # релевантная запись в ЧУЖОЙ ветке
    lib.record(_scope("br-2"), _spec("инъекция в поле ввода формы", "отражение ввода"))

    hits = lib.retrieve(scope, query="инъекция форма ввод", limit=10)
    techniques = [h.spec.technique for h in hits]
    assert techniques[0].startswith("инъекция")  # релевантная — первой
    assert all("перебор" not in t for t in techniques[:1])
    # чужая ветка не возвращается
    assert all(h.scope_key.endswith(":br-1:t") or ":br-1:" in h.scope_key for h in hits)


def test_scope_isolation() -> None:
    lib = _lib()
    lib.record(_scope("br-2"), _spec("инъекция в форму", "отражение"))
    hits = lib.retrieve(_scope("br-1"), query="инъекция форма", limit=10)
    assert hits == []  # чужая ветка недоступна


def test_learning_tie_break_by_success() -> None:
    lib = _lib()
    scope = _scope()
    a = lib.record(scope, _spec("инъекция в форму", "отражение"))
    b = lib.record(scope, _spec("инъекция в форму", "отражение"))
    # одинаково релевантны; у B выше доля успеха
    lib.note_outcome(scope, a.strategy_id, supported=False)
    lib.note_outcome(scope, b.strategy_id, supported=True)
    lib.note_outcome(scope, b.strategy_id, supported=True)
    hits = lib.retrieve(scope, query="инъекция форма", limit=10)
    assert hits[0].strategy_id == b.strategy_id  # успешная стратегия выше при равной релевантности


def test_no_match_empty() -> None:
    lib = _lib()
    lib.record(_scope(), _spec("перебор заказов", "доступ"))
    assert lib.retrieve(_scope(), query="совершенно иное", limit=10) == []
