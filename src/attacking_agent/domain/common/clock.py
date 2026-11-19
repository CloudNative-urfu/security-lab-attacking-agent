"""Инъектируемые часы для детерминированных тестов TTL и таймаутов."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Порт времени."""

    def now(self) -> datetime: ...


class SystemClock:
    """Системные часы в UTC (production-умолчание)."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class ManualClock:
    """Управляемые часы для тестов."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        from datetime import timedelta

        self._now = self._now + timedelta(seconds=seconds)
