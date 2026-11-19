"""Контракт инструмента и реестр.

Инструмент описывается типизированным контрактом (имя, доменная операция, модель
параметров, исполнитель, поле адреса). Контракт не даёт права на исполнение — он лишь
проверяемое описание; исполнение всегда проходит через диспетчер и изолированный runner.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

# Исполнитель инструмента: получает изолированный контекст и провалидированные параметры.
ToolExecutor = Callable[["ToolContext", Any], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ToolContract:
    name: str
    operation: str
    params_model: type[BaseModel]
    executor: ToolExecutor
    address_field: str | None = None
    """Имя поля параметров, содержащего адрес назначения (для проверки политики)."""


class ToolContext:
    """Изолированный контекст, передаваемый исполнителю инструмента.

    Контекст намеренно НЕ содержит доверенных секретов (ключей моделей, credentials БД,
    snapshot-control). Он предоставляет только адрес назначения и счётчик подзапросов с
    конечным cap.
    """

    __slots__ = ("_cap", "_count", "address")

    def __init__(self, address: str | None, subrequest_cap: int) -> None:
        self.address = address
        self._cap = subrequest_cap
        self._count = 0

    def subrequest(self) -> None:
        """Зарегистрировать физический подзапрос; при превышении cap — ошибка."""
        self._count += 1
        if self._count > self._cap:
            raise SubrequestCapError(
                f"превышен cap подзапросов ({self._cap}); redirects и WebSocket не бесплатны"
            )

    @property
    def subrequest_count(self) -> int:
        return self._count


class SubrequestCapError(RuntimeError):
    """Инструмент превысил разрешённое число физических подзапросов."""


class ToolRegistry:
    """Реестр контрактов инструментов."""

    def __init__(self) -> None:
        self._contracts: dict[str, ToolContract] = {}

    def register(self, contract: ToolContract) -> None:
        self._contracts[contract.name] = contract

    def get(self, name: str) -> ToolContract | None:
        return self._contracts.get(name)
