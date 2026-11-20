"""Роли памяти: Reader, Writer, Controller.

Публикует в память только Writer, атомарно под блокировкой ветки, после повторной
проверки approval по candidate hash. Reader читает в режимах TASK/ANALYSIS, всегда с
фильтром области. Controller проверяет применимость и формирует план курации, но не
пишет в хранилище напрямую — проверенный кандидат передаётся Writer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from attacking_agent.domain.common.ids import IdFactory, UuidIdFactory
from attacking_agent.domain.common.scope import Scope
from attacking_agent.domain.memory.entities import (
    Assessment,
    Candidate,
    CandidateState,
    CurationPlan,
    KnowledgeInput,
    KnowledgeNode,
    ReadMode,
    composition_hash,
)
from attacking_agent.domain.memory.store import MemoryStore, scope_key


class ReadStatus(StrEnum):
    OK = "ok"
    NO_MATCH = "no_match"
    INCOMPLETE = "incomplete"
    DENIED = "denied"


class CommitStatus(StrEnum):
    PUBLISHED = "published"
    VERSION_CONFLICT = "version_conflict"
    STALE = "stale"
    REJECTED = "rejected"
    DEFERRED = "deferred"
    NO_CHANGE = "no_change"


class AppendStatus(StrEnum):
    APPENDED = "appended"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class ReadPackage:
    status: ReadStatus
    mode: ReadMode
    snapshot_version: int
    knowledge: list[KnowledgeNode]


@dataclass(frozen=True, slots=True)
class AppendResult:
    status: AppendStatus
    event_id: str


@dataclass(frozen=True, slots=True)
class CommitResult:
    status: CommitStatus
    receipt_id: str | None = None
    snapshot_version: int | None = None


class Reader:
    """Чтение памяти в режимах TASK/ANALYSIS с обязательным фильтром области."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def read(self, scope: Scope, mode: ReadMode, query: str, limit: int) -> ReadPackage:
        version = self._store.branch_version(scope)
        # Источник — только зафиксированные записи текущей ветки (кандидаты-preview
        # сюда не попадают; чужая ветка недоступна по построению ключа области).
        nodes = self._store.committed_knowledge(scope)
        matched = [n for n in nodes if query.lower() in n.statement.lower()]
        if mode is ReadMode.TASK:
            matched = [n for n in matched if not n.superseded]
        matched = matched[:limit]
        if matched:
            return ReadPackage(ReadStatus.OK, mode, version, matched)
        # Нет совпадений: при нехватке данных — INCOMPLETE/NO_MATCH, но не чужие записи.
        status = ReadStatus.NO_MATCH if not nodes else ReadStatus.INCOMPLETE
        return ReadPackage(status, mode, version, [])


class Writer:
    """Единственный путь публикации в память."""

    def __init__(self, store: MemoryStore, id_factory: IdFactory | None = None) -> None:
        self._store = store
        self._ids = id_factory or UuidIdFactory()

    def append_event(self, scope: Scope, event) -> AppendResult:  # type: ignore[no-untyped-def]
        status = self._store.append_event(scope, event)
        return AppendResult(AppendStatus(status.value), event.event_id)

    def prepare(
        self,
        scope: Scope,
        base_version: int,
        read_set: list[str],
        knowledge: list[KnowledgeInput],
    ) -> Candidate:
        chash = composition_hash(scope_key(scope), base_version, read_set, knowledge)
        candidate = Candidate(
            candidate_id=self._ids.new_id("cand"),
            scope_key=scope_key(scope),
            base_version=base_version,
            read_set=list(read_set),
            composition_hash=chash,
            knowledge=list(knowledge),
            state=CandidateState.PREPARED,
        )
        self._store.put_candidate(candidate)
        return candidate

    def evaluate(self, scope: Scope, candidate: Candidate) -> Assessment:
        # Оценка привязана к составу кандидата; изменение состава аннулирует approval.
        return Assessment(
            assessment_ref=self._ids.new_id("assess"),
            candidate_id=candidate.candidate_id,
            composition_hash=candidate.composition_hash,
            verdict="accepted",
        )

    def commit(self, scope: Scope, candidate: Candidate, assessment: Assessment) -> CommitResult:
        with self._store.lock(scope):
            # Идемпотентность: тот же кандидат уже закоммичен — прежняя квитанция.
            prior = self._store.get_committed_receipt(candidate.candidate_id)
            if prior is not None:
                version = self._store.branch_version(scope)
                return CommitResult(CommitStatus.PUBLISHED, prior, version)
            # Approval привязан к составу: изменение аннулирует одобрение.
            if candidate.composition_hash != assessment.composition_hash:
                return CommitResult(CommitStatus.REJECTED)
            if assessment.candidate_id != candidate.candidate_id:
                return CommitResult(CommitStatus.REJECTED)
            # Проверка fence по базовой версии.
            if candidate.base_version != self._store.branch_version(scope):
                return CommitResult(CommitStatus.VERSION_CONFLICT)
            nodes = [
                KnowledgeNode(
                    node_id=self._ids.new_id("know"),
                    version=0,  # присвоится в commit_knowledge
                    scope_key=candidate.scope_key,
                    statement=k.statement,
                    status=k.status,
                    conditions=list(k.conditions),
                    evidence_refs=list(k.evidence_refs),
                    counterexamples=list(k.counterexamples),
                    applicability=k.applicability,
                )
                for k in candidate.knowledge
            ]
            new_version = self._store.commit_knowledge(scope, nodes)
            receipt_id = self._ids.new_id("pubrcpt")
            self._store.mark_committed(candidate.candidate_id, receipt_id)
            return CommitResult(CommitStatus.PUBLISHED, receipt_id, new_version)


class Controller:
    """Проверка применимости и курация; прямой записи в хранилище не имеет."""

    can_write_directly = False

    def __init__(self, store: MemoryStore) -> None:
        self._store = store
        self._reader = Reader(store)

    def check_applicability(self, scope: Scope, query: str) -> ReadPackage:
        return self._reader.read(scope, ReadMode.ANALYSIS, query, limit=100)

    def plan_curation(self, scope: Scope) -> CurationPlan:
        # Читает накопленную область (ANALYSIS) и формирует план для Writer.
        nodes = self._store.committed_knowledge(scope)
        operations = [f"review:{n.node_id}" for n in nodes]
        return CurationPlan(
            scope_key=scope_key(scope),
            base_version=self._store.branch_version(scope),
            read_set=[n.node_id for n in nodes],
            operations=operations,
        )
