"""Tool-runner: сетевой сервис изолированного исполнения инструментов.

Принимает к запуску ТОЛЬКО инструменты из явного allowlist (по идентичности кода) —
защита от RCE. Исполнение — через `SubprocessToolRunner` (отдельный процесс, санация env,
таймаут, cap вывода). Секреты вызывающего здесь недоступны.
"""

from __future__ import annotations

import importlib
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from attacking_agent.adapters.subprocess_runner import SubprocessToolRunner
from attacking_agent.domain.tools import RunRequest, ToolRunner

# Идентичность инструмента: (module, qualname).
ToolId = tuple[str, str]


class RunBody(BaseModel):
    executor_module: str
    executor_qualname: str
    params_module: str
    params_qualname: str
    params: dict[str, Any] = Field(default_factory=dict)
    address: str | None = None


def _resolve(module: str, qualname: str) -> Any:
    obj: Any = importlib.import_module(module)
    for part in qualname.split("."):
        obj = getattr(obj, part)
    return obj


def create_runner_app(
    allowlist: set[ToolId],
    runner: ToolRunner | None = None,
) -> FastAPI:
    app = FastAPI(title="Полигон · Tool-runner", version="0.1.0")
    _runner = runner or SubprocessToolRunner()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/run")
    def run(body: RunBody) -> dict[str, Any]:
        tool_id: ToolId = (body.executor_module, body.executor_qualname)
        if tool_id not in allowlist:
            raise HTTPException(status_code=403, detail="инструмент вне allowlist")
        try:
            executor = _resolve(body.executor_module, body.executor_qualname)
            model = _resolve(body.params_module, body.params_qualname)
            params = model.model_validate(body.params)
        except (ImportError, AttributeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"не разрешается: {exc}") from exc
        obs = _runner.run(RunRequest(executor=executor, params=params, address=body.address))
        return {
            "status": obs.status.value,
            "payload": obs.payload,
            "subrequests": obs.subrequests,
            "error": obs.error,
        }

    return app
