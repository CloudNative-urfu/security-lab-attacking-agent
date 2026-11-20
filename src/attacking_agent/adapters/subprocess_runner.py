"""Субпроцессная изоляция исполнения инструмента (порт :class:`ToolRunner`).

Инструмент исполняется в отдельном процессе (``spawn``) с санированным окружением (без
секретных переменных), wall-clock таймаутом и cap на размер вывода. Это даёт изоляцию
памяти и секретов родителя. Честная граница: произвольный недоверенный код всё ещё может
открыть сокет внутри процесса — сетевой периметр (egress-namespace/gVisor) остаётся
отдельным gate и не выдаётся за достигнутый здесь.
"""

from __future__ import annotations

import importlib
import json
import multiprocessing as mp
import os
from dataclasses import dataclass
from queue import Empty
from typing import Any

from attacking_agent.domain.tools import Observation, ObservationStatus
from attacking_agent.domain.tools.contracts import SubrequestCapError, ToolContext
from attacking_agent.domain.tools.runner import RunRequest

# Имена переменных окружения, вычищаемых в дочернем процессе перед запуском инструмента.
_SECRET_ENV_MARKERS = (
    "SECRET",
    "TOKEN",
    "KEY",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "DATABASE_URL",
    "DB_URL",
    "DSN",
    "AWS_",
    "API",
)

_AMBIGUOUS = ("TimeoutError", "ConnectionError")


@dataclass(frozen=True, slots=True)
class _ChildSpec:
    executor_module: str
    executor_qualname: str
    params_module: str
    params_qualname: str
    params_dict: dict[str, Any]
    address: str | None
    cap: int
    scrub_markers: tuple[str, ...]


def _scrub_env(markers: tuple[str, ...]) -> None:
    for name in list(os.environ.keys()):
        upper = name.upper()
        if any(m in upper for m in markers):
            del os.environ[name]


def _resolve(module: str, qualname: str) -> Any:
    obj: Any = importlib.import_module(module)
    for part in qualname.split("."):
        obj = getattr(obj, part)
    return obj


def _child_entry(result_q: mp.Queue[dict[str, Any]], spec: _ChildSpec) -> None:
    _scrub_env(spec.scrub_markers)
    ctx = ToolContext(address=spec.address, subrequest_cap=spec.cap)
    try:
        executor = _resolve(spec.executor_module, spec.executor_qualname)
        model = _resolve(spec.params_module, spec.params_qualname)
        params = model.model_validate(spec.params_dict)
        payload = executor(ctx, params)
        result_q.put(
            {"status": "ok", "payload": payload, "subrequests": ctx.subrequest_count}
        )
    except SubrequestCapError as exc:
        result_q.put(
            {"status": "error", "subrequests": ctx.subrequest_count, "error": str(exc)}
        )
    except BaseException as exc:
        kind = "unknown" if type(exc).__name__ in _AMBIGUOUS else "error"
        result_q.put(
            {"status": kind, "subrequests": ctx.subrequest_count, "error": repr(exc)}
        )


class SubprocessToolRunner:
    """Исполняет module-level инструмент в изолированном дочернем процессе."""

    def __init__(
        self,
        subrequest_cap: int = 16,
        timeout_seconds: float = 10.0,
        max_output_bytes: int = 256 * 1024,
        scrub_markers: tuple[str, ...] = _SECRET_ENV_MARKERS,
    ) -> None:
        self._cap = subrequest_cap
        self._timeout = timeout_seconds
        self._max_output = max_output_bytes
        self._scrub = scrub_markers
        self._ctx = mp.get_context("spawn")

    def run(self, request: RunRequest) -> Observation:
        executor = request.executor
        params = request.params
        spec = _ChildSpec(
            executor_module=getattr(executor, "__module__", ""),
            executor_qualname=getattr(executor, "__qualname__", ""),
            params_module=type(params).__module__,
            params_qualname=type(params).__qualname__,
            params_dict=params.model_dump(),
            address=request.address,
            cap=self._cap,
            scrub_markers=self._scrub,
        )
        if not spec.executor_module or not spec.executor_qualname:
            return Observation(
                ObservationStatus.ERROR, {}, 0,
                error="исполнитель не адресуется по имени (нужна module-level функция)",
            )

        result_q: mp.Queue[dict[str, Any]] = self._ctx.Queue()
        proc = self._ctx.Process(target=_child_entry, args=(result_q, spec))
        proc.start()
        proc.join(self._timeout)
        if proc.is_alive():
            proc.terminate()
            proc.join()
            return Observation(
                ObservationStatus.UNKNOWN, {}, 0,
                error=f"таймаут инструмента ({self._timeout}s); процесс прекращён",
            )
        try:
            result = result_q.get_nowait()
        except Empty:
            return Observation(
                ObservationStatus.ERROR, {}, 0, error="инструмент не вернул результат"
            )

        subreq = int(result.get("subrequests", 0))
        if result["status"] == "ok":
            payload = result.get("payload", {})
            if len(json.dumps(payload).encode("utf-8")) > self._max_output:
                return Observation(
                    ObservationStatus.ERROR, {}, subreq,
                    error=f"превышен cap размера вывода ({self._max_output} байт)",
                )
            return Observation(ObservationStatus.OK, payload, subreq)
        status = (
            ObservationStatus.UNKNOWN if result["status"] == "unknown" else ObservationStatus.ERROR
        )
        return Observation(status, {}, subreq, error=result.get("error"))
