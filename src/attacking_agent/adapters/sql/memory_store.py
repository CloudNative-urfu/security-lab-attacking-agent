"""SQLAlchemy-реализация порта :class:`MemoryStore` (источник истины памяти).

Секционирование по ключу области (ветка). ``lock(scope)`` открывает транзакцию (на
SQLite сериализуется процессно; на PostgreSQL — row locks/advisory). Чтение работает и
вне ``lock()`` короткой сессией. pgvector — отдельное production-расширение поиска.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, delete, select, text
from sqlalchemy.orm import Session, sessionmaker

from attacking_agent.adapters.sql.budget_store import configure_sqlite_engine
from attacking_agent.domain.common.scope import Scope
from attacking_agent.domain.memory.entities import Candidate, EventInput, KnowledgeNode
from attacking_agent.domain.memory.store import AppendStatus, scope_key

from .models import (
    BranchVersionRow,
    CommittedCandidateRow,
    EventRow,
    KnowledgeRow,
)

# База advisory-ключа памяти для PostgreSQL (гранулярность — по ветке через hashtext).
_PG_MEMORY_LOCK_NS = 0x4D_45_4D_30  # "MEM0"


def _to_node(row: KnowledgeRow) -> KnowledgeNode:
    return KnowledgeNode(
        node_id=row.node_id,
        version=row.version,
        scope_key=row.branch_key,
        statement=row.statement,
        status=row.status,
        conditions=list(row.conditions),
        evidence_refs=list(row.evidence_refs),
        counterexamples=list(row.counterexamples),
        applicability=row.applicability,
        superseded=row.superseded,
    )


class SQLAlchemyMemoryStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._dialect = engine.dialect.name
        self._session_factory = sessionmaker(engine)
        self._local = threading.local()
        self._guard = threading.Lock()
        configure_sqlite_engine(engine)

    def _acquire_db_lock(self, session: Session, scope: Scope) -> None:
        # Кросс-процессная сериализация публикации в ветку (branch-fence).
        if self._dialect == "postgresql":
            session.execute(
                text("SELECT pg_advisory_xact_lock(:ns, hashtext(:k))"),
                {"ns": _PG_MEMORY_LOCK_NS, "k": scope_key(scope)},
            )
        elif self._dialect == "sqlite":
            session.execute(text("SELECT 1"))  # форсирует BEGIN IMMEDIATE

    @contextmanager
    def lock(self, scope: Scope) -> Iterator[None]:
        if getattr(self._local, "session", None) is not None:
            yield
            return
        self._guard.acquire()
        session = self._session_factory()
        self._local.session = session
        try:
            self._acquire_db_lock(session, scope)
            yield
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()
            self._local.session = None
            self._guard.release()

    @contextmanager
    def _op(self) -> Iterator[Session]:
        borrowed: Session | None = getattr(self._local, "session", None)
        if borrowed is not None:
            yield borrowed
            return
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def branch_version(self, scope: Scope) -> int:
        with self._op() as session:
            row = session.get(BranchVersionRow, scope_key(scope))
            return row.version if row is not None else 0

    def append_event(self, scope: Scope, event: EventInput) -> AppendStatus:
        key = scope_key(scope)
        with self._op() as session:
            existing = session.get(EventRow, (key, event.event_id))
            if existing is not None:
                if existing.hash == event.hash:
                    return AppendStatus.DUPLICATE
                return AppendStatus.CONFLICT
            session.add(
                EventRow(
                    branch_key=key,
                    event_id=event.event_id,
                    hash=event.hash,
                    source=event.source,
                    kind=event.kind,
                    payload=dict(event.payload),
                    episode_id=event.episode_id,
                    op_id=event.op_id,
                )
            )
            return AppendStatus.APPENDED

    def committed_knowledge(self, scope: Scope) -> list[KnowledgeNode]:
        key = scope_key(scope)
        with self._op() as session:
            rows = session.scalars(
                select(KnowledgeRow).where(KnowledgeRow.branch_key == key)
            )
            return [_to_node(r) for r in rows]

    def snapshot_knowledge(self, scope: Scope) -> list[KnowledgeNode]:
        return self.committed_knowledge(scope)

    def commit_knowledge(self, scope: Scope, nodes: list[KnowledgeNode]) -> int:
        key = scope_key(scope)
        with self._op() as session:
            ver_row = session.get(BranchVersionRow, key)
            if ver_row is None:
                ver_row = BranchVersionRow(branch_key=key, version=0)
                session.add(ver_row)
            new_version = ver_row.version + 1
            ver_row.version = new_version
            for node in nodes:
                session.add(
                    KnowledgeRow(
                        node_id=node.node_id,
                        branch_key=key,
                        version=new_version,
                        statement=node.statement,
                        status=node.status,
                        conditions=list(node.conditions),
                        evidence_refs=list(node.evidence_refs),
                        counterexamples=list(node.counterexamples),
                        applicability=node.applicability,
                        superseded=node.superseded,
                    )
                )
            return new_version

    def seed_branch(self, scope: Scope, nodes: list[KnowledgeNode]) -> int:
        key = scope_key(scope)
        with self._op() as session:
            session.execute(delete(KnowledgeRow).where(KnowledgeRow.branch_key == key))
            version = max((n.version for n in nodes), default=0)
            for node in nodes:
                session.add(
                    KnowledgeRow(
                        node_id=node.node_id,
                        branch_key=key,
                        version=node.version,
                        statement=node.statement,
                        status=node.status,
                        conditions=list(node.conditions),
                        evidence_refs=list(node.evidence_refs),
                        counterexamples=list(node.counterexamples),
                        applicability=node.applicability,
                        superseded=node.superseded,
                    )
                )
            ver_row = session.get(BranchVersionRow, key)
            if ver_row is None:
                ver_row = BranchVersionRow(branch_key=key, version=version)
                session.add(ver_row)
            else:
                ver_row.version = version
            return version

    def put_candidate(self, candidate: Candidate) -> None:
        # Кандидаты (preview) не являются источником истины; для MVP не персистим их
        # отдельно — commit публикует только проверенный состав. Оставлено как no-op,
        # совместимый с портом; production может хранить preview в отдельной таблице.
        return None

    def get_committed_receipt(self, candidate_id: str) -> str | None:
        with self._op() as session:
            row = session.get(CommittedCandidateRow, candidate_id)
            return row.receipt_id if row is not None else None

    def mark_committed(self, candidate_id: str, receipt_id: str) -> None:
        with self._op() as session:
            if session.get(CommittedCandidateRow, candidate_id) is None:
                session.add(
                    CommittedCandidateRow(candidate_id=candidate_id, receipt_id=receipt_id)
                )
