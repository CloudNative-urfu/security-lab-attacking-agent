"""Безопасный HTTP-инструмент поверх порта инструментов.

Исполнитель регистрирует каждый сетевой вызов как физический подзапрос (ctx.subrequest),
поэтому redirects и повторы считаются бюджетом и ограничены cap runner. Автоматические
redirects отключены: перенаправление возвращается как наблюдение и проходит повторную
проверку политики на следующем шаге, а не выполняется молча.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, Field

from attacking_agent.domain.tools import ToolContext, ToolContract


class HttpParams(BaseModel):
    url: str = Field(min_length=1)
    method: str = "GET"
    body: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)


class HttpToolExecutor:
    """Исполнитель HTTP-инструмента с явным клиентом и без скрытых повторов/redirect."""

    def __init__(self, client: httpx.Client, max_body_chars: int = 4096) -> None:
        self._client = client
        self._max_body = max_body_chars

    def __call__(self, ctx: ToolContext, params: HttpParams) -> dict[str, Any]:
        ctx.subrequest()  # каждый вызов — учтённый физический подзапрос
        response = self._client.request(
            params.method.upper(),
            params.url,
            content=params.body,
            headers=params.headers,
            follow_redirects=False,
        )
        return {
            "status_code": response.status_code,
            "redirect_location": response.headers.get("location"),
            "body": response.text[: self._max_body],
        }


def make_http_tool(
    name: str,
    operation: str,
    client: httpx.Client,
    address_field: str = "url",
) -> ToolContract:
    """Собрать контракт HTTP-инструмента для регистрации в реестре диспетчера."""
    return ToolContract(
        name=name,
        operation=operation,
        params_model=HttpParams,
        executor=HttpToolExecutor(client),
        address_field=address_field,
    )
