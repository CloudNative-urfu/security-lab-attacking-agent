"""Контракт capability semantic-memory: Reader, Writer, Controller."""

from __future__ import annotations

from attacking_agent.domain.common.scope import Scope
from attacking_agent.domain.memory import (
    AppendStatus,
    CommitStatus,
    Controller,
    EventInput,
    InMemoryMemoryStore,
    KnowledgeInput,
    Reader,
    ReadMode,
    ReadStatus,
    Writer,
)


def _scope(branch: str = "br-1") -> Scope:
    return Scope(campaign_id="camp-1", branch_id=branch, target_ref="svc-demo")


def _store() -> InMemoryMemoryStore:
    return InMemoryMemoryStore()


def _event(event_id: str = "ev-1", hash_: str = "h1") -> EventInput:
    return EventInput(
        event_id=event_id,
        hash=hash_,
        source="tool",
        kind="observation",
        payload={"status_code": 200},
        episode_id="ep-1",
        op_id="op-1",
    )


def _knowledge(statement: str = "ввод отражается без экранирования") -> KnowledgeInput:
    return KnowledgeInput(
        statement=statement,
        status="hypothesis",
        conditions=["эндпойнт /feedback"],
        evidence_refs=["ev-1"],
        counterexamples=[],
    )


# --- Writer: Events append-only -------------------------------------------------


def test_append_event_dedup_same_id_hash() -> None:
    store = _store()
    writer = Writer(store)
    r1 = writer.append_event(_scope(), _event())
    r2 = writer.append_event(_scope(), _event())
    assert r1.status is AppendStatus.APPENDED
    assert r2.status is AppendStatus.DUPLICATE
    assert r1.event_id == r2.event_id


def test_append_event_same_id_diff_hash_conflict() -> None:
    store = _store()
    writer = Writer(store)
    writer.append_event(_scope(), _event("ev-1", "h1"))
    r = writer.append_event(_scope(), _event("ev-1", "h2"))
    assert r.status is AppendStatus.CONFLICT


# --- Writer: PREPARE -> EVALUATE -> COMMIT --------------------------------------


def _prepare_commit(store: InMemoryMemoryStore, scope: Scope) -> object:
    writer = Writer(store)
    base = store.branch_version(scope)
    candidate = writer.prepare(
        scope, base_version=base, read_set=["ev-1"], knowledge=[_knowledge()]
    )
    assessment = writer.evaluate(scope, candidate)
    return writer.commit(scope, candidate, assessment)


def test_prepare_evaluate_commit_publishes() -> None:
    store = _store()
    scope = _scope()
    receipt = _prepare_commit(store, scope)
    assert receipt.status is CommitStatus.PUBLISHED  # type: ignore[attr-defined]
    # опубликованное знание доступно обычному TASK-чтению
    reader = Reader(store)
    pkg = reader.read(scope, ReadMode.TASK, query="отражается", limit=10)
    assert pkg.status is ReadStatus.OK
    assert any("отражается" in k.statement for k in pkg.knowledge)


def test_recommit_same_candidate_returns_prior_receipt() -> None:
    store = _store()
    scope = _scope()
    writer = Writer(store)
    base = store.branch_version(scope)
    candidate = writer.prepare(scope, base_version=base, read_set=[], knowledge=[_knowledge()])
    assessment = writer.evaluate(scope, candidate)
    r1 = writer.commit(scope, candidate, assessment)
    r2 = writer.commit(scope, candidate, assessment)
    assert r1.status is CommitStatus.PUBLISHED
    assert r2.status is CommitStatus.PUBLISHED
    assert r1.receipt_id == r2.receipt_id  # прежняя квитанция, не VERSION_CONFLICT


def test_changed_composition_invalidates_approval() -> None:
    store = _store()
    scope = _scope()
    writer = Writer(store)
    base = store.branch_version(scope)
    candidate = writer.prepare(scope, base_version=base, read_set=[], knowledge=[_knowledge()])
    assessment = writer.evaluate(scope, candidate)
    tampered = candidate.with_tampered_composition()  # type: ignore[attr-defined]
    r = writer.commit(scope, tampered, assessment)
    assert r.status in (CommitStatus.REJECTED, CommitStatus.STALE)


# --- Reader: режимы и изоляция --------------------------------------------------


def test_task_does_not_see_candidate_preview() -> None:
    store = _store()
    scope = _scope()
    writer = Writer(store)
    base = store.branch_version(scope)
    # подготовлен, но не закоммичен — это preview
    writer.prepare(scope, base_version=base, read_set=[], knowledge=[_knowledge("preview-знание")])
    reader = Reader(store)
    pkg = reader.read(scope, ReadMode.TASK, query="preview", limit=10)
    assert all("preview-знание" not in k.statement for k in pkg.knowledge)


def test_reader_does_not_cross_branch() -> None:
    store = _store()
    scope_a = _scope("br-1")
    scope_b = _scope("br-2")
    _prepare_commit(store, scope_b)  # знание только в ветке B
    reader = Reader(store)
    for mode in (ReadMode.TASK, ReadMode.ANALYSIS):
        pkg = reader.read(scope_a, mode, query="отражается", limit=10)
        assert pkg.knowledge == []  # ветка A не видит записи ветки B


def test_insufficient_results_incomplete_not_other_branch() -> None:
    store = _store()
    scope_a = _scope("br-1")
    scope_b = _scope("br-2")
    _prepare_commit(store, scope_b)
    reader = Reader(store)
    pkg = reader.read(scope_a, ReadMode.ANALYSIS, query="отражается", limit=10)
    assert pkg.status in (ReadStatus.NO_MATCH, ReadStatus.INCOMPLETE)
    assert pkg.knowledge == []  # никогда не выдаём чужие записи


# --- Controller: не пишет напрямую ----------------------------------------------


def test_controller_does_not_write_directly() -> None:
    store = _store()
    scope = _scope()
    # накопим подтверждённое знание
    _prepare_commit(store, scope)
    controller = Controller(store)
    plan = controller.plan_curation(scope)
    # план курации — это кандидат для Writer, Controller сам в БД не пишет
    assert controller.can_write_directly is False  # type: ignore[attr-defined]
    before = store.branch_version(scope)
    assert plan is not None
    # версия ветки не изменилась от одного планирования
    assert store.branch_version(scope) == before


def test_controller_api_has_no_store_mutation() -> None:
    # У Controller нет метода прямой записи в хранилище.
    assert not hasattr(Controller, "commit")
    assert not hasattr(Controller, "append_event")
    assert not hasattr(Controller(_store()), "write_knowledge")
