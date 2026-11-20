"""Контроль метрик: признаки по наблюдениям.

Признаки рассчитываются по Events, но исход эпизода (SUPPORTED/REFUTED/INCONCLUSIVE)
определяет контроллер, а stop_reason — отдельное поле. Скрытая бизнес-оценка полигона
здесь не хранится и не возвращается в контекст АГ.
"""

from __future__ import annotations

from attacking_agent.domain.tools import Observation


class MetricsControl:
    def __init__(self) -> None:
        self._signals: list[dict[str, object]] = []

    def record(self, observation: Observation) -> None:
        self._signals.append(
            {
                "status": observation.status.value,
                "subrequests": observation.subrequests,
                "has_payload": bool(observation.payload),
            }
        )

    @property
    def signals(self) -> list[dict[str, object]]:
        return list(self._signals)
