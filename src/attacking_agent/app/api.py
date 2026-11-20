"""FastAPI-приложение операторского интерфейса.

Командный путь (start/pause/resume/fork) идемпотентен по command_id; read-модели
отдаются только после авторизации. UI/экспорт не пишут в доменные таблицы и не
содержат секретов.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from attacking_agent.app.services import (
    AuthProvider,
    CommandService,
    CommandStatus,
    Principal,
    ReadModelService,
)


class CommandBody(BaseModel):
    command_id: str = Field(min_length=1)
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)


def _extract_token(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:]
    return None


def create_app(
    command_service: CommandService,
    read_models: ReadModelService,
    auth: AuthProvider,
) -> FastAPI:
    app = FastAPI(title="Полигон · Операторский API АГ", version="0.1.0")

    def require_principal(authorization: str | None = Header(default=None)) -> Principal:
        principal = auth.authenticate(_extract_token(authorization))
        if principal is None:
            raise HTTPException(status_code=401, detail="требуется авторизация")
        return principal

    @app.post("/campaigns/{campaign_id}/commands")
    def submit_command(campaign_id: str, body: CommandBody) -> dict[str, str]:
        try:
            result = command_service.submit(
                campaign_id, body.command_id, body.kind, body.payload
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if result.status is CommandStatus.CONFLICT:
            raise HTTPException(status_code=409, detail="конфликт command_id с другим содержимым")
        return {"status": result.status.value, "workflow_ref": result.workflow_ref}

    @app.get("/campaigns/{campaign_id}/state")
    def get_state(
        campaign_id: str, principal: Principal = Depends(require_principal)
    ) -> dict[str, Any]:
        state = read_models.get_state(campaign_id)
        if state is None:
            raise HTTPException(status_code=404, detail="кампания не найдена")
        return state

    @app.get("/campaigns/{campaign_id}/metrics")
    def get_metrics(
        campaign_id: str, principal: Principal = Depends(require_principal)
    ) -> dict[str, Any]:
        return read_models.get_metrics(campaign_id)

    @app.get("/campaigns/{campaign_id}/export")
    def export(
        campaign_id: str, principal: Principal = Depends(require_principal)
    ) -> dict[str, Any]:
        data = read_models.export(campaign_id)
        if data is None:
            raise HTTPException(status_code=404, detail="кампания не найдена")
        return data

    return app
