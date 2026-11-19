"""Изолированный исполнитель инструментов (runner).

Runner исполняет недоверенный код инструмента в отдельном контексте без доверенных
секретов, с конечным cap на подзапросы. Порт позволяет заменить локальный runner на
усиленную изоляцию (containerd + gVisor) без изменения домена.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from attacking_agent.domain.tools.contracts import (
    SubrequestCapError,
    ToolContext,
    ToolExecutor,
)
from attacking_agent.domain.tools.journal import Observation, ObservationStatus

# Исключения, означающие неоднозначный исход (мог быть физический эффект).
_AMBIGUOUS_EXCEPTIONS = (TimeoutError, ConnectionError)


@dataclass(frozen=True, slots=True)
class RunRequest:
    executor: ToolExecutor
    params: BaseModel
    address: str | None


class ToolRunner(Protocol):
    """Порт изолированного исполнения инструмента."""

    def run(self, request: RunRequest) -> Observation: ...


class LocalToolRunner:
    """Локальный runner: исполнение в процессе с cap подзапросов, без секретов.

    Используется для MVP и тестов. Производственная замена (отдельный процесс/sandbox)
    реализует тот же порт :class:`ToolRunner`.
    """

    def __init__(self, subrequest_cap: int = 16) -> None:
        self._cap = subrequest_cap

    def run(self, request: RunRequest) -> Observation:
        ctx = ToolContext(address=request.address, subrequest_cap=self._cap)
        try:
            payload = request.executor(ctx, request.params)
        except SubrequestCapError as exc:
            return Observation(
                ObservationStatus.ERROR, {}, ctx.subrequest_count, error=str(exc)
            )
        except _AMBIGUOUS_EXCEPTIONS as exc:
            return Observation(
                ObservationStatus.UNKNOWN, {}, ctx.subrequest_count, error=str(exc)
            )
        except Exception as exc:
            return Observation(
                ObservationStatus.ERROR, {}, ctx.subrequest_count, error=str(exc)
            )
        return Observation(ObservationStatus.OK, payload, ctx.subrequest_count)
