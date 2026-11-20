"""LLM-шлюз: сетевой сервис транспорта к модели.

Чистый транспорт поверх доменного порта `LLMClient` (LiteLLM в production, fake в тестах):
маршрутизация провайдеров, без скрытых retry. Бюджет и журнал здесь НЕ ведутся — они
остаются у вызывающего (единый источник истины).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from attacking_agent.domain.llm import LLMClient, LLMRequest


class CompleteBody(BaseModel):
    op_id: str
    attempt_id: str
    initiator: str
    model_ref: str
    messages: list[dict[str, str]] = Field(default_factory=list)
    max_tokens: int = 256


def create_gateway_app(client: LLMClient) -> FastAPI:
    app = FastAPI(title="Полигон · LLM-шлюз", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/complete")
    def complete(body: CompleteBody) -> dict[str, Any]:
        req = LLMRequest(
            op_id=body.op_id,
            attempt_id=body.attempt_id,
            initiator=body.initiator,
            model_ref=body.model_ref,
            messages=body.messages,
            max_tokens=body.max_tokens,
        )
        try:
            response = client.complete(req)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"ошибка провайдера: {exc}") from exc
        return {
            "content": response.content,
            "structured": response.structured,
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
            },
            "cost": str(response.cost),
        }

    return app
