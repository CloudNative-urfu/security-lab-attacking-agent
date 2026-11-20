"""Тонкий durable-граф эпизода на реальном Temporal: эпизод как Workflow с пошаговыми Activity."""

from __future__ import annotations

import asyncio
import uuid

import pytest

pytestmark = pytest.mark.integration

try:
    from decimal import Decimal

    from temporalio.client import Client

    from attacking_agent.adapters.temporal_fine_grained import FineGrainedTemporalEngine
    from attacking_agent.domain.budget import (
        BudgetAccountRef,
        InMemoryBudgetStore,
        LedgerManager,
    )
    from attacking_agent.domain.common.clock import ManualClock
    from attacking_agent.domain.common.ids import SequentialIdFactory
    from attacking_agent.domain.common.scope import Scope
    from attacking_agent.domain.config import BudgetLevel, BudgetType, Hardness, PolicySpec
    from attacking_agent.domain.llm import LLMProxy, LLMRequest, LLMResponse, Usage
    from attacking_agent.domain.memory import InMemoryMemoryStore, Reader, Writer
    from attacking_agent.domain.orchestration import (
        EpisodeOutcome,
        EpisodeSpec,
        MetricsControl,
        StepDecision,
    )
    from attacking_agent.domain.orchestration.step_executor import EpisodeStepExecutor
    from attacking_agent.domain.policy import PolicyEnforcer, StaticAddressResolver
    from attacking_agent.domain.tools import (
        Dispatcher,
        InMemoryCallJournal,
        LocalToolRunner,
        ToolContext,
        ToolContract,
        ToolRegistry,
    )
    from pydantic import BaseModel

    _OK = True
except Exception:  # pragma: no cover
    _OK = False

TEMPORAL = "localhost:7233"


def _executor(runs: list[str]) -> "EpisodeStepExecutor":
    class _P(BaseModel):
        url: str

    scope = Scope(campaign_id="c", branch_id="b", target_ref="t")
    mgr = LedgerManager(
        InMemoryBudgetStore(), clock=ManualClock(), id_factory=SequentialIdFactory()
    )
    money = BudgetAccountRef("c", BudgetLevel.CAMPAIGN, "c", BudgetType.MONEY)
    reqs = BudgetAccountRef("c", BudgetLevel.CAMPAIGN, "c", BudgetType.REQUESTS)
    mgr.open_account(money, limit=Decimal("1"), hardness=Hardness.HARD, currency="USD")
    mgr.open_account(reqs, limit=Decimal("50"), hardness=Hardness.HARD)

    def tool(ctx: ToolContext, params: _P) -> dict:
        runs.append(params.url)
        return {"status_code": 200}

    registry = ToolRegistry()
    registry.register(
        ToolContract(name="probe", operation="http.post", params_model=_P,
                     executor=tool, address_field="url")
    )
    policy = PolicyEnforcer(
        PolicySpec(policy_version="p1", allowed_operations=["http.post"],
                   allowed_addresses=["https://demo.svc.internal/*"]),
        resolver=StaticAddressResolver({"demo.svc.internal": ["203.0.113.10"]}),
    )
    dispatcher = Dispatcher(policy=policy, ledger=mgr, journal=InMemoryCallJournal(),
                            runner=LocalToolRunner(), registry=registry)
    mem = InMemoryMemoryStore()

    class _LLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, request: LLMRequest) -> LLMResponse:
            d = (
                StepDecision(kind="ACTION", operation="http.post", tool="probe",
                             params={"url": "https://demo.svc.internal/feedback"})
                if self.calls == 0
                else StepDecision(kind="FINISH", outcome="SUPPORTED")
            )
            self.calls += 1
            return LLMResponse(content="", structured=d.model_dump(),
                               usage=Usage(prompt_tokens=5, completion_tokens=5),
                               cost=Decimal("0.001"))

    proxy = LLMProxy(client=_LLM(), ledger=mgr)
    return EpisodeStepExecutor(
        reader=Reader(mem), writer=Writer(mem, id_factory=SequentialIdFactory()),
        proxy=proxy, dispatcher=dispatcher, policy=policy, metrics=MetricsControl(),
        llm_account=money, tool_account=reqs,
        llm_reserve=Decimal("0.05"), tool_reserve=Decimal("1"),
    )


def test_fine_grained_workflow_on_real_temporal() -> None:
    if not _OK:
        pytest.skip("temporalio/зависимости недоступны")

    async def run() -> tuple[str, str, int, int]:
        try:
            client = await Client.connect(TEMPORAL, namespace="default")
        except Exception as exc:
            pytest.skip(f"Temporal недоступен по {TEMPORAL}: {exc}")
        runs: list[str] = []
        engine = FineGrainedTemporalEngine(client, task_queue=f"fg-{uuid.uuid4().hex[:8]}")
        spec = EpisodeSpec(f"ep-{uuid.uuid4().hex[:8]}",
                           Scope(campaign_id="c", branch_id="b", target_ref="t"),
                           "вопрос", max_steps=5)
        result = await engine.execute(_executor(runs), spec)
        return result.outcome.value, result.stop_reason, result.steps_taken, len(runs)

    outcome, stop, steps, runs = asyncio.run(run())
    assert outcome == EpisodeOutcome.SUPPORTED.value
    assert stop == "finished"
    assert steps == 2  # эпизод прошёл как пошаговый durable-граф
    assert runs == 1  # ровно одна физическая отправка через пошаговую Activity
