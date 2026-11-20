"""Тест production-адаптера TemporalExecutionEngine на in-memory Temporal (мок).

Используется официальный ``WorkflowEnvironment`` (встроенный тест-сервер с time-skipping) —
без docker и внешнего Temporal. Доменный runner эпизода замокан, чтобы проверять именно
адаптер: сериализацию spec/result, оркестрацию Workflow→Activity и устойчивый повтор.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import uuid
from collections.abc import AsyncIterator

import pytest

pytestmark = pytest.mark.integration

try:
    from temporalio.client import Client
    from temporalio.testing import WorkflowEnvironment

    from attacking_agent.adapters.temporal_engine import TemporalExecutionEngine
    from attacking_agent.domain.common.scope import Scope
    from attacking_agent.domain.orchestration import (
        EpisodeOutcome,
        EpisodeResult,
        EpisodeSpec,
    )

    _TEMPORAL_OK = True
except Exception:  # pragma: no cover
    _TEMPORAL_OK = False


def _spec() -> EpisodeSpec:
    return EpisodeSpec(
        episode_id=f"ep-{uuid.uuid4().hex[:8]}",
        scope=Scope(campaign_id="c", branch_id="b", target_ref="t"),
        question="есть ли отражение ввода",
        max_steps=5,
    )


class _OkRunner:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, spec: EpisodeSpec) -> EpisodeResult:
        self.calls += 1
        return EpisodeResult(EpisodeOutcome.SUPPORTED, "finished", 2)


class _FlakyRunner:
    """Падает на первой попытке Activity, успешно отрабатывает со второй."""

    def __init__(self) -> None:
        self.calls = 0

    def run(self, spec: EpisodeSpec) -> EpisodeResult:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("временный сбой эпизода")
        return EpisodeResult(EpisodeOutcome.REFUTED, "finished", 3)


@contextlib.asynccontextmanager
async def _client() -> AsyncIterator[Client]:
    """In-memory тест-сервер, иначе — реальный docker-Temporal (fallback для NixOS)."""
    env = None
    with contextlib.suppress(Exception):
        env = await WorkflowEnvironment.start_time_skipping()
    if env is not None:
        async with env:
            yield env.client
        return
    addr = os.environ.get("AG_TEMPORAL", "localhost:7233")
    try:
        client = await Client.connect(addr, namespace="default")
    except Exception as exc:
        pytest.skip(f"Temporal недоступен (ни in-memory, ни {addr}): {exc}")
    yield client


def test_engine_happy_path_returns_result() -> None:
    if not _TEMPORAL_OK:
        pytest.skip("temporalio недоступен")

    async def run() -> None:
        async with _client() as client:
            engine = TemporalExecutionEngine(client, task_queue=f"tq-{uuid.uuid4().hex[:8]}")
            runner = _OkRunner()
            result = await engine.execute(runner, _spec())
            assert result.outcome is EpisodeOutcome.SUPPORTED
            assert result.stop_reason == "finished"
            assert result.steps_taken == 2
            assert runner.calls == 1

    asyncio.run(run())


def test_engine_retries_flaky_episode_and_completes() -> None:
    if not _TEMPORAL_OK:
        pytest.skip("temporalio недоступен")

    async def run() -> None:
        async with _client() as client:
            engine = TemporalExecutionEngine(
                client, task_queue=f"tq-{uuid.uuid4().hex[:8]}", max_attempts=3
            )
            runner = _FlakyRunner()
            result = await engine.execute(runner, _spec())
            assert result.outcome is EpisodeOutcome.REFUTED  # вернулся результат 2-й попытки
            assert runner.calls == 2  # Temporal реально повторил Activity

    asyncio.run(run())
