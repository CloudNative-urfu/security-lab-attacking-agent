"""Сетевой адаптер порта :class:`LLMClient` — транспорт к LLM-шлюзу по HTTP.

Домен (`LLMProxy`) не меняется: бюджет и журнал остаются у вызывающего, адаптер лишь
переносит один вызов модели через сетевую границу. Decimal-cost передаётся строкой, чтобы
сохранить точность.
"""

from __future__ import annotations

from decimal import Decimal

import httpx

from attacking_agent.domain.llm import LLMRequest, LLMResponse, Usage


class HttpLLMClient:
    """Реализация `LLMClient`, обращающаяся к llm-gateway (`POST /v1/complete`)."""

    def __init__(
        self, base_url: str, client: httpx.Client | None = None, timeout: float = 60.0
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(base_url=self._base_url, timeout=timeout)
        self._owns_client = client is None

    def complete(self, request: LLMRequest) -> LLMResponse:
        resp = self._client.post(
            "/v1/complete",
            json={
                "op_id": request.op_id,
                "attempt_id": request.attempt_id,
                "initiator": request.initiator,
                "model_ref": request.model_ref,
                "messages": request.messages,
                "max_tokens": request.max_tokens,
            },
        )
        if resp.status_code != 200:
            raise RuntimeError(f"llm-gateway вернул {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        usage = data.get("usage") or {}
        return LLMResponse(
            content=str(data.get("content", "")),
            structured=data.get("structured"),
            usage=Usage(
                prompt_tokens=int(usage.get("prompt_tokens", 0)),
                completion_tokens=int(usage.get("completion_tokens", 0)),
            ),
            cost=Decimal(str(data.get("cost", "0"))),
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
