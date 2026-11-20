"""Durable журнал попыток модели по (op_id, attempt_id).

Повтор обработки той же попытки возвращает записанный ответ без нового физического
вызова провайдера. Атомарный ``claim`` исключает двойную отправку при конкуренции.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from attacking_agent.domain.llm.proxy import LLMResponse


class LLMJournal(Protocol):
    """Порт журнала попыток модели."""

    def get(self, op_id: str, attempt_id: str) -> LLMResponse | None: ...
    def claim(self, op_id: str, attempt_id: str) -> bool: ...
    def record(self, op_id: str, attempt_id: str, response: LLMResponse) -> None: ...


class InMemoryLLMJournal:
    """In-memory реализация :class:`LLMJournal`."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._claims: set[tuple[str, str]] = set()
        self._responses: dict[tuple[str, str], LLMResponse] = {}

    def get(self, op_id: str, attempt_id: str) -> LLMResponse | None:
        with self._lock:
            return self._responses.get((op_id, attempt_id))

    def claim(self, op_id: str, attempt_id: str) -> bool:
        key = (op_id, attempt_id)
        with self._lock:
            if key in self._claims:
                return False
            self._claims.add(key)
            return True

    def record(self, op_id: str, attempt_id: str, response: LLMResponse) -> None:
        with self._lock:
            self._responses[(op_id, attempt_id)] = response
