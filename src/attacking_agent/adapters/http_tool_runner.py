"""Сетевой адаптер порта :class:`ToolRunner` — исполнение инструмента удалённым сервисом.

Диспетчер (политика, бюджет, журнал) остаётся у вызывающего; через границу передаётся
только идентичность инструмента (module+qualname) и параметры, но НЕ исполняемый код.
Удалённый runner-сервис проверяет идентичность по allowlist и исполняет изолированно.
"""

from __future__ import annotations

import httpx

from attacking_agent.domain.tools import Observation, ObservationStatus, RunRequest


class HttpToolRunner:
    """Реализация `ToolRunner`, обращающаяся к tool-runner сервису (`POST /v1/run`)."""

    def __init__(
        self, base_url: str, client: httpx.Client | None = None, timeout: float = 30.0
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(base_url=self._base_url, timeout=timeout)
        self._owns_client = client is None

    def run(self, request: RunRequest) -> Observation:
        executor = request.executor
        params = request.params
        body = {
            "executor_module": getattr(executor, "__module__", ""),
            "executor_qualname": getattr(executor, "__qualname__", ""),
            "params_module": type(params).__module__,
            "params_qualname": type(params).__qualname__,
            "params": params.model_dump(),
            "address": request.address,
        }
        try:
            resp = self._client.post("/v1/run", json=body)
        except httpx.HTTPError as exc:
            return Observation(ObservationStatus.UNKNOWN, {}, 0, error=f"runner недоступен: {exc}")
        if resp.status_code == 403:
            return Observation(ObservationStatus.ERROR, {}, 0, error="инструмент вне allowlist")
        if resp.status_code != 200:
            return Observation(
                ObservationStatus.ERROR, {}, 0, error=f"runner вернул {resp.status_code}"
            )
        data = resp.json()
        return Observation(
            status=ObservationStatus(data["status"]),
            payload=data.get("payload") or {},
            subrequests=int(data.get("subrequests", 0)),
            error=data.get("error"),
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
