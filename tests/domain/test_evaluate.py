"""Реальная оценка кандидата при публикации (не штамп accept)."""

from __future__ import annotations

from attacking_agent.domain.common.ids import UuidIdFactory
from attacking_agent.domain.common.scope import Scope
from attacking_agent.domain.memory import (
    InMemoryMemoryStore,
    KnowledgeInput,
    Writer,
)
from attacking_agent.domain.memory.roles import CommitStatus


def _scope() -> Scope:
    return Scope(campaign_id="c", branch_id="b", target_ref="t")


def _writer(store: InMemoryMemoryStore) -> Writer:
    return Writer(store, id_factory=UuidIdFactory())


def test_consistent_candidate_accepted_and_published() -> None:
    store = InMemoryMemoryStore()
    scope = _scope()
    w = _writer(store)
    cand = w.prepare(scope, base_version=0, read_set=["ev-1"],
                     knowledge=[KnowledgeInput(statement="гипотеза", evidence_refs=["ev-1"])])
    assessment = w.evaluate(scope, cand)
    assert assessment.verdict == "accepted"
    assert w.commit(scope, cand, assessment).status is CommitStatus.PUBLISHED


def test_stale_base_rejected() -> None:
    store = InMemoryMemoryStore()
    scope = _scope()
    w = _writer(store)
    cand = w.prepare(scope, base_version=0, read_set=[],
                     knowledge=[KnowledgeInput(statement="k")])
    # ветка продвинулась после prepare -> база кандидата устарела
    other = w.prepare(scope, base_version=0, read_set=[],
                      knowledge=[KnowledgeInput(statement="другое")])
    w.commit(scope, other, w.evaluate(scope, other))  # версия ветки теперь 1
    assessment = w.evaluate(scope, cand)
    assert assessment.verdict == "rejected"
    assert w.commit(scope, cand, assessment).status in (
        CommitStatus.REJECTED, CommitStatus.VERSION_CONFLICT
    )


def test_confirmed_without_evidence_rejected() -> None:
    store = InMemoryMemoryStore()
    scope = _scope()
    w = _writer(store)
    cand = w.prepare(scope, base_version=0, read_set=[],
                     knowledge=[KnowledgeInput(statement="подтверждено", status="confirmed",
                                               evidence_refs=[])])
    assessment = w.evaluate(scope, cand)
    assert assessment.verdict == "rejected"
    assert w.commit(scope, cand, assessment).status is CommitStatus.REJECTED


def test_empty_statement_rejected() -> None:
    store = InMemoryMemoryStore()
    scope = _scope()
    w = _writer(store)
    cand = w.prepare(scope, base_version=0, read_set=[],
                     knowledge=[KnowledgeInput(statement="   ")])
    assert w.evaluate(scope, cand).verdict == "rejected"
