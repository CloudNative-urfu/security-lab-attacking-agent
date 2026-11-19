"""Журнал вызовов: durable intent/attempt/result.

Журнал — доменные записи источника истины, а не sampled-trace и не отдельный сервис,
куда «потом продублируем». Записи идемпотентны по (op_id, attempt_id).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Protocol


class ObservationStatus(StrEnum):
    OK = "ok"
    REFUSED = "refused"
    UNKNOWN = "unknown"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Observation:
    status: ObservationStatus
    payload: dict[str, Any]
    subrequests: int = 0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class IntentRecord:
    op_id: str
    attempt_id: str
    branch_id: str
    tool_name: str
    permit_id: str | None


@dataclass(slots=True)
class ResultRecord:
    op_id: str
    attempt_id: str
    branch_id: str
    observation: Observation
    dispatch_status: str
    reconciled: bool = False
    final_status: ObservationStatus | None = None


class CallJournal(Protocol):
    """Порт журнала вызовов."""

    def record_intent(self, intent: IntentRecord) -> None: ...
    def has_intent(self, op_id: str, attempt_id: str) -> bool: ...
    def record_result(self, result: ResultRecord) -> None: ...
    def get_result(self, op_id: str, attempt_id: str) -> ResultRecord | None: ...
    def reconcile(self, op_id: str, attempt_id: str, final_status: ObservationStatus) -> None: ...
    def unresolved_unknowns(self, branch_id: str) -> list[ResultRecord]: ...


class InMemoryCallJournal:
    """In-memory реализация :class:`CallJournal`."""

    def __init__(self) -> None:
        self._intents: dict[tuple[str, str], IntentRecord] = {}
        self._results: dict[tuple[str, str], ResultRecord] = {}

    def record_intent(self, intent: IntentRecord) -> None:
        key = (intent.op_id, intent.attempt_id)
        self._intents.setdefault(key, intent)

    def has_intent(self, op_id: str, attempt_id: str) -> bool:
        return (op_id, attempt_id) in self._intents

    def record_result(self, result: ResultRecord) -> None:
        key = (result.op_id, result.attempt_id)
        self._results.setdefault(key, replace(result))

    def get_result(self, op_id: str, attempt_id: str) -> ResultRecord | None:
        r = self._results.get((op_id, attempt_id))
        return replace(r) if r is not None else None

    def reconcile(self, op_id: str, attempt_id: str, final_status: ObservationStatus) -> None:
        r = self._results.get((op_id, attempt_id))
        if r is not None:
            r.reconciled = True
            r.final_status = final_status

    def unresolved_unknowns(self, branch_id: str) -> list[ResultRecord]:
        return [
            replace(r)
            for r in self._results.values()
            if r.branch_id == branch_id
            and r.observation.status is ObservationStatus.UNKNOWN
            and not r.reconciled
        ]
