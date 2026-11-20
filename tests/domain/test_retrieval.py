"""Ранжирующий retriever: релевантность, фильтр области, замена подстроки."""

from __future__ import annotations

from attacking_agent.domain.common.ids import UuidIdFactory
from attacking_agent.domain.common.scope import Scope
from attacking_agent.domain.memory import (
    InMemoryMemoryStore,
    KnowledgeInput,
    Reader,
    ReadMode,
    Writer,
)
from attacking_agent.domain.memory.retrieval import TokenOverlapScorer, cosine


def _scope(branch: str = "br-1") -> Scope:
    return Scope(campaign_id="c", branch_id=branch, target_ref="t")


def _commit(store: InMemoryMemoryStore, scope: Scope, statement: str) -> None:
    w = Writer(store, id_factory=UuidIdFactory())
    base = store.branch_version(scope)
    cand = w.prepare(scope, base_version=base, read_set=[],
                     knowledge=[KnowledgeInput(statement=statement)])
    w.commit(scope, cand, w.evaluate(scope, cand))


def test_more_relevant_ranked_higher() -> None:
    store = InMemoryMemoryStore()
    scope = _scope()
    _commit(store, scope, "форма обратной связи отражает ввод пользователя")
    _commit(store, scope, "ввод присутствует где-то")
    _commit(store, scope, "таймаут сети на эндпойнте оплаты")
    reader = Reader(store)
    pkg = reader.read(scope, ReadMode.TASK, query="ввод форма", limit=10)
    statements = [k.statement for k in pkg.knowledge]
    # запись с перекрытием двух термов ("ввод","форма") идёт раньше записи с одним
    assert statements[0].startswith("форма обратной связи")
    # нерелевантная запись (нет общих термов) не возвращается
    assert all("оплаты" not in s for s in statements)


def test_token_overlap_matches_where_substring_would_not() -> None:
    store = InMemoryMemoryStore()
    scope = _scope()
    _commit(store, scope, "эндпойнт отражает ввод при POST")
    reader = Reader(store)
    # как цельная подстрока "ввод отражает" в тексте нет (другой порядок), но токены совпадают
    pkg = reader.read(scope, ReadMode.TASK, query="ввод отражает", limit=10)
    assert len(pkg.knowledge) == 1


def test_scope_filter_preserved_under_ranking() -> None:
    store = InMemoryMemoryStore()
    _commit(store, _scope("br-2"), "форма отражает ввод")
    reader = Reader(store)
    pkg = reader.read(_scope("br-1"), ReadMode.ANALYSIS, query="ввод форма", limit=10)
    assert pkg.knowledge == []  # чужая ветка не возвращается при любом ранжировании


def test_scorer_zero_for_no_overlap() -> None:
    s = TokenOverlapScorer()
    assert s.score("ввод форма", "совершенно другой текст") == 0.0
    assert s.score("ввод", "обнаружен ввод данных") > 0.0


def test_cosine_basic() -> None:
    assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
