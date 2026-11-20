"""Порт хранилища памяти и in-memory реализация.

Хранилище секционировано по ключу области (кампания/ветка/цель): чтение в одной ветке
не видит записи другой. Источник истины — зафиксированные (committed) записи; кандидаты
(preview) хранятся отдельно и не попадают в обычное чтение.
"""

from __future__ import annotations

import threading
from contextlib import AbstractContextManager
from dataclasses import replace
from enum import StrEnum
from typing import Protocol

from attacking_agent.domain.common.scope import Scope
from attacking_agent.domain.memory.entities import (
    Candidate,
    Event,
    EventInput,
    KnowledgeNode,
)


class AppendStatus(StrEnum):
    APPENDED = "appended"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"


def scope_key(scope: Scope) -> str:
    return f"{scope.campaign_id}:{scope.branch_id}:{scope.target_ref}"


class MemoryStore(Protocol):
    """Порт хранилища памяти."""

    def lock(self, scope: Scope) -> AbstractContextManager[object]: ...
    def branch_version(self, scope: Scope) -> int: ...
    def append_event(self, scope: Scope, event: EventInput) -> AppendStatus: ...
    def committed_knowledge(self, scope: Scope) -> list[KnowledgeNode]: ...
    def commit_knowledge(self, scope: Scope, nodes: list[KnowledgeNode]) -> int: ...
    def put_candidate(self, candidate: Candidate) -> None: ...
    def get_committed_receipt(self, candidate_id: str) -> str | None: ...
    def mark_committed(self, candidate_id: str, receipt_id: str) -> None: ...


class InMemoryMemoryStore:
    """In-memory реализация :class:`MemoryStore`."""

    def __init__(self) -> None:
        self._locks: dict[str, threading.RLock] = {}
        self._events: dict[str, dict[str, Event]] = {}
        self._knowledge: dict[str, list[KnowledgeNode]] = {}
        self._versions: dict[str, int] = {}
        self._candidates: dict[str, Candidate] = {}
        self._committed: dict[str, str] = {}
        self._guard = threading.RLock()

    def lock(self, scope: Scope) -> AbstractContextManager[object]:
        key = scope_key(scope)
        with self._guard:
            if key not in self._locks:
                self._locks[key] = threading.RLock()
            return self._locks[key]

    def branch_version(self, scope: Scope) -> int:
        return self._versions.get(scope_key(scope), 0)

    def append_event(self, scope: Scope, event: EventInput) -> AppendStatus:
        key = scope_key(scope)
        bucket = self._events.setdefault(key, {})
        existing = bucket.get(event.event_id)
        if existing is not None:
            if existing.hash == event.hash:
                return AppendStatus.DUPLICATE
            return AppendStatus.CONFLICT
        bucket[event.event_id] = Event(
            event_id=event.event_id,
            hash=event.hash,
            scope_key=key,
            source=event.source,
            kind=event.kind,
            payload=dict(event.payload),
            episode_id=event.episode_id,
            op_id=event.op_id,
        )
        return AppendStatus.APPENDED

    def committed_knowledge(self, scope: Scope) -> list[KnowledgeNode]:
        return [replace(n) for n in self._knowledge.get(scope_key(scope), [])]

    def commit_knowledge(self, scope: Scope, nodes: list[KnowledgeNode]) -> int:
        key = scope_key(scope)
        new_version = self._versions.get(key, 0) + 1
        bucket = self._knowledge.setdefault(key, [])
        for node in nodes:
            bucket.append(replace(node, version=new_version))
        self._versions[key] = new_version
        return new_version

    def put_candidate(self, candidate: Candidate) -> None:
        self._candidates[candidate.candidate_id] = candidate

    def get_committed_receipt(self, candidate_id: str) -> str | None:
        return self._committed.get(candidate_id)

    def mark_committed(self, candidate_id: str, receipt_id: str) -> None:
        self._committed[candidate_id] = receipt_id
