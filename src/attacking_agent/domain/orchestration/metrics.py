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
        self._refusals: list[dict[str, object]] = []

    def record(self, observation: Observation) -> None:
        self._signals.append(
            {
                "status": observation.status.value,
                "subrequests": observation.subrequests,
                "has_payload": bool(observation.payload),
            }
        )

    def record_refusal(self, reason: str, raw: str) -> None:
        """Зафиксировать отказ модели выдать решение по схеме — сигнал силы guardrail СС/модели."""
        self._refusals.append({"reason": reason, "raw": raw})

    @property
    def signals(self) -> list[dict[str, object]]:
        return list(self._signals)

    @property
    def refusals(self) -> list[dict[str, object]]:
        return list(self._refusals)
