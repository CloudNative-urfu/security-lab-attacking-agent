"""Эталонные/демонстрационные исполнители инструментов (импортируемы по имени).

Используются демо, тестами изоляции и как референс. Это module-level функции, чтобы их
можно было запускать в отдельном процессе (:class:`SubprocessToolRunner`).
"""

from __future__ import annotations

import os
import time
from typing import Any

from pydantic import BaseModel, Field

from attacking_agent.domain.tools import ToolContext


class EchoParams(BaseModel):
    body: str = ""


def echo_tool(ctx: ToolContext, params: EchoParams) -> dict[str, Any]:
    """Отражает ввод (исследуемое поведение заглушки СС)."""
    ctx.subrequest()
    return {"status_code": 200, "reflected": params.body}


class EnvProbeParams(BaseModel):
    var: str = "AG_TEST_SECRET"


def env_probe_tool(ctx: ToolContext, params: EnvProbeParams) -> dict[str, Any]:
    """Пытается прочитать секрет из окружения — для проверки санации env в subprocess."""
    return {"saw_secret": os.environ.get(params.var) is not None}


class SleepParams(BaseModel):
    seconds: float = 0.0


def sleep_tool(ctx: ToolContext, params: SleepParams) -> dict[str, Any]:
    """Спит заданное время — для проверки таймаута runner."""
    time.sleep(params.seconds)
    return {"slept": params.seconds}


class SizeParams(BaseModel):
    size: int = Field(default=16, ge=0)


def big_output_tool(ctx: ToolContext, params: SizeParams) -> dict[str, Any]:
    """Возвращает вывод заданного размера — для проверки cap на размер вывода."""
    return {"data": "x" * params.size}
