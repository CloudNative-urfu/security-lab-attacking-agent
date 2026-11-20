"""Порт движка исполнения и локальная реализация.

Движок владеет историей/replay эпизода. Для MVP — локальный in-process движок;
производственная замена (Temporal Workflow/Child Workflow через официальный bridge)
реализует тот же порт без изменения доменной логики цикла эпизода.
"""

from __future__ import annotations

from typing import Protocol

from attacking_agent.domain.orchestration.controllers import EpisodeController
from attacking_agent.domain.orchestration.models import EpisodeResult, EpisodeSpec


class ExecutionEngine(Protocol):
    """Порт устойчивого исполнения эпизода."""

    def run(self, controller: EpisodeController, spec: EpisodeSpec) -> EpisodeResult: ...


class LocalExecutionEngine:
    """Локальный детерминированный движок (in-process)."""

    def run(self, controller: EpisodeController, spec: EpisodeSpec) -> EpisodeResult:
        return controller.run(spec)
