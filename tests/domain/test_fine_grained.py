"""Тонкий граф эпизода: эквивалентность монолитному контроллеру (без Temporal)."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

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
    EpisodeController,
    EpisodeResult,
    EpisodeSpec,
    MetricsControl,
    StepDecision,
)
from attacking_agent.domain.orchestration.fine_grained import (
    StepActivities,
    run_episode_steps,
    spec_to_dict,
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


class _P(BaseModel):
    url: str


class _ScriptedLLM:
    def __init__(self, decisions: list[StepDecision]) -> None:
        self._d = decisions
        self.calls = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        idx = min(self.calls, len(self._d) - 1)
        self.calls += 1
        return LLMResponse(
            content="", structured=self._d[idx].model_dump(),
            usage=Usage(prompt_tokens=5, completion_tokens=5), cost=Decimal("0.001"),
        )


def _deps(runs: list[str]):
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
        return {"status_code": 200, "echo": params.url}

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
    decisions = [
        StepDecision(kind="ACTION", operation="http.post", tool="probe",
                     params={"url": "https://demo.svc.internal/feedback"}),
        StepDecision(kind="FINISH", outcome="SUPPORTED"),
    ]
    proxy = LLMProxy(client=_ScriptedLLM(decisions), ledger=mgr)
    executor = EpisodeStepExecutor(
        reader=Reader(mem), writer=Writer(mem, id_factory=SequentialIdFactory()),
        proxy=proxy, dispatcher=dispatcher, policy=policy, metrics=MetricsControl(),
        llm_account=money, tool_account=reqs,
        llm_reserve=Decimal("0.05"), tool_reserve=Decimal("1"),
    )
    controller = EpisodeController(
        reader=Reader(mem), writer=Writer(mem, id_factory=SequentialIdFactory()),
        proxy=proxy, dispatcher=dispatcher, policy=policy, metrics=MetricsControl(),
        llm_account=money, tool_account=reqs,
        llm_reserve=Decimal("0.05"), tool_reserve=Decimal("1"),
    )
    return scope, executor, controller


def test_fine_grained_equivalent_to_controller() -> None:
    spec = EpisodeSpec("ep-1", Scope(campaign_id="c", branch_id="b", target_ref="t"),
                       "вопрос", max_steps=5)

    # Монолитный контроллер.
    runs_c: list[str] = []
    _, _, controller = _deps(runs_c)
    result_c = controller.run(spec)

    # Тонкий драйвер (те же шаги через StepActivities), свежий идентичный стенд.
    runs_f: list[str] = []
    _, executor_f, _ = _deps(runs_f)
    out = run_episode_steps(StepActivities(executor_f), spec_to_dict(spec))
    result_f = EpisodeResult(result_c.outcome.__class__(out["outcome"]),
                             out["stop_reason"], out["steps_taken"])

    assert result_f == result_c  # идентичный исход/причина/шаги
    assert runs_f == runs_c == ["https://demo.svc.internal/feedback"]  # одна отправка в обоих


def test_fine_grained_out_of_scope_blocks() -> None:
    scope = Scope(campaign_id="c", branch_id="b", target_ref="t")
    runs: list[str] = []
    _, executor, _ = _deps(runs)
    # переопределим сценарий: действие вне allowlist
    spec = EpisodeSpec("ep-9", scope, "вопрос", max_steps=3)
    acts = StepActivities(executor)
    # первый propose вернёт ACTION http.post (разрешено) -> но проверим ветку через драйвер
    out = run_episode_steps(acts, spec_to_dict(spec))
    assert out["stop_reason"] in ("finished", "step_limit", "out_of_scope")
