"""Типизированные идентификаторы и их фабрика.

Все физические эффекты адресуются стабильными идентификаторами. Генерация новых
идентификаторов инъектируется через :class:`IdFactory`, чтобы домен оставался
детерминированным и тестируемым.
"""

from __future__ import annotations

import uuid
from typing import NewType, Protocol

# Идентичности областей и жизненного цикла.
CampaignId = NewType("CampaignId", str)
BranchId = NewType("BranchId", str)
EpisodeId = NewType("EpisodeId", str)
CommandId = NewType("CommandId", str)

# Идентичности намерения и физической попытки.
OpId = NewType("OpId", str)
AttemptId = NewType("AttemptId", str)
ReservationId = NewType("ReservationId", str)
ChargeId = NewType("ChargeId", str)

# Идентичности памяти.
EventId = NewType("EventId", str)
CandidateId = NewType("CandidateId", str)
NodeId = NewType("NodeId", str)
EdgeId = NewType("EdgeId", str)


class IdFactory(Protocol):
    """Порт генерации свежих идентификаторов."""

    def new_id(self, prefix: str) -> str: ...


class UuidIdFactory:
    """Фабрика на основе uuid4 (production-умолчание)."""

    def new_id(self, prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex}"


class SequentialIdFactory:
    """Детерминированная фабрика для тестов: монотонный счётчик на префикс."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def new_id(self, prefix: str) -> str:
        n = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = n
        return f"{prefix}-{n:06d}"
