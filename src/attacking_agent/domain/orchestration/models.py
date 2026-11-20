"""Модели эпизода и решений шага."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from attacking_agent.domain.common.scope import Scope


class EpisodeOutcome(StrEnum):
    SUPPORTED = "SUPPORTED"
    REFUTED = "REFUTED"
    INCONCLUSIVE = "INCONCLUSIVE"


class StepDecision(BaseModel):
    """Структурированное предложение модели на шаге эпизода."""

    model_config = ConfigDict(frozen=True)
    kind: Literal["ACTION", "FINISH", "NEED_EVIDENCE", "INCONCLUSIVE"]
    operation: str = ""
    tool: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    hypothesis: str = ""
    outcome: Literal["SUPPORTED", "REFUTED", "INCONCLUSIVE"] = "INCONCLUSIVE"


@dataclass(frozen=True, slots=True)
class EpisodeSpec:
    episode_id: str
    scope: Scope
    question: str
    max_steps: int = 10


@dataclass(frozen=True, slots=True)
class EpisodeResult:
    outcome: EpisodeOutcome
    stop_reason: str
    steps_taken: int
