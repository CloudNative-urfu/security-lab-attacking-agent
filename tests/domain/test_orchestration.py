"""Контракт capability campaign-orchestration: цикл эпизода и контроллер кампании."""

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
from attacking_agent.domain.memory import (
    InMemoryMemoryStore,
    Reader,
    Writer,
)
from attacking_agent.domain.orchestration import (
    CampaignController,
    EpisodeController,
    EpisodeOutcome,
    EpisodeSpec,
    LocalExecutionEngine,
    MetricsControl,
    OpenStatus,
    StepDecision,
)
from attacking_agent.domain.policy import PolicyEnforcer, StaticAddressResolver
from attacking_agent.domain.tools import (
    Dispatcher,
    InMemoryCallJournal,
    LocalToolRunner,
    ToolContext,
    ToolContract,
    ToolRegistry,
)


class _Params(BaseModel):
    url: str


class _ScriptedLLM:
    """Выдаёт заранее заданные решения как структурированный ответ."""

    def __init__(self, decisions: list[StepDecision]) -> None:
        self._decisions = decisions
        self.calls = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        idx = min(self.calls, len(self._decisions) - 1)
        self.calls += 1
        return LLMResponse(
            content="",
            structured=self._decisions[idx].model_dump(),
            usage=Usage(prompt_tokens=10, completion_tokens=10),
            cost=Decimal("0.001"),
        )


class Harness:
    def __init__(self, decisions: list[StepDecision], fail_passport: bool = False) -> None:
        self.scope = Scope(campaign_id="camp-1", branch_id="br-1", target_ref="svc-demo")
        self.store = InMemoryBudgetStore()
        self.mgr = LedgerManager(self.store, clock=ManualClock(), id_factory=SequentialIdFactory())
        self.llm_acc = self._acc(BudgetType.MONEY, "1.00", "USD")
        self.tool_acc = self._acc(BudgetType.REQUESTS, "50", None)
        self.runner = LocalToolRunner()
        self.runs: list[str] = []

        def tool_fn(ctx: ToolContext, params: _Params) -> dict:
            self.runs.append(params.url)
            return {"status_code": 200}

        self.registry = ToolRegistry()
        self.registry.register(
            ToolContract(
                name="http_post",
                operation="http.post",
                params_model=_Params,
                executor=tool_fn,
                address_field="url",
            )
        )
        self.policy = PolicyEnforcer(
            PolicySpec(
                policy_version="p1",
                allowed_operations=["http.post"],
                allowed_addresses=["https://demo.svc.internal/*"],
            ),
            resolver=StaticAddressResolver({"demo.svc.internal": ["203.0.113.10"]}),
        )
        self.journal = InMemoryCallJournal()
        self.dispatcher = Dispatcher(
            policy=self.policy,
            ledger=self.mgr,
            journal=self.journal,
            runner=self.runner,
            registry=self.registry,
        )
        self.mem = InMemoryMemoryStore()
        self.reader = Reader(self.mem)
        self.writer = Writer(self.mem, id_factory=SequentialIdFactory())
        if fail_passport:
            self._seed_passport_conflict()
        self.proxy = LLMProxy(client=_ScriptedLLM(decisions), ledger=self.mgr)
        self.metrics = MetricsControl()
        self.controller = EpisodeController(
            reader=self.reader,
            writer=self.writer,
            proxy=self.proxy,
            dispatcher=self.dispatcher,
            policy=self.policy,
            metrics=self.metrics,
            llm_account=self.llm_acc,
            tool_account=self.tool_acc,
            llm_reserve=Decimal("0.05"),
            tool_reserve=Decimal("1"),
        )

    def _acc(self, bt: BudgetType, limit: str, cur: str | None) -> BudgetAccountRef:
        ref = BudgetAccountRef(
            campaign_id="camp-1", level=BudgetLevel.CAMPAIGN, scope_key="camp-1", budget_type=bt
        )
        self.mgr.open_account(ref, limit=Decimal(limit), hardness=Hardness.HARD, currency=cur)
        return ref

    def _seed_passport_conflict(self) -> None:
        from attacking_agent.domain.memory import EventInput

        # Пресеем событие с id паспорта шага 0, но другим hash -> append вернёт CONFLICT.
        self.writer.append_event(
            self.scope,
            EventInput(
                event_id="ep-1-passport-0", hash="OTHER", source="seed", kind="step", payload={}
            ),
        )

    def spec(self, episode_id: str = "ep-1", max_steps: int = 5) -> EpisodeSpec:
        return EpisodeSpec(
            episode_id=episode_id,
            scope=self.scope,
            question="есть ли отражение ввода",
            max_steps=max_steps,
        )


def _action(url: str = "https://demo.svc.internal/feedback") -> StepDecision:
    return StepDecision(kind="ACTION", operation="http.post", tool="http_post", params={"url": url})


def _finish(outcome: str = "SUPPORTED") -> StepDecision:
    return StepDecision(kind="FINISH", outcome=outcome)


def test_single_active_episode_per_branch() -> None:
    h = Harness([_finish()])
    campaign = CampaignController()
    r1 = campaign.open_episode(h.scope, "ep-1")
    assert r1.status is OpenStatus.OK
    r2 = campaign.open_episode(h.scope, "ep-2")
    assert r2.status is OpenStatus.REJECTED
    campaign.close_episode(h.scope, "ep-1")
    r3 = campaign.open_episode(h.scope, "ep-2")
    assert r3.status is OpenStatus.OK


def test_action_then_finish_runs_tool() -> None:
    h = Harness([_action(), _finish("SUPPORTED")])
    result = h.controller.run(h.spec())
    assert result.outcome is EpisodeOutcome.SUPPORTED
    assert result.stop_reason == "finished"
    assert h.runs == ["https://demo.svc.internal/feedback"]


def test_step_passport_before_action() -> None:
    # Если паспорт шага не зафиксирован (CONFLICT), действие не передаётся инструменту.
    h = Harness([_action(), _finish()], fail_passport=True)
    h.controller.run(h.spec())
    assert h.runs == []  # инструмент не вызван без квитанции паспорта


def test_proposal_outside_kik_rejected() -> None:
    # Операция вне allowlist КИК -> действие не выполняется.
    h = Harness([StepDecision(kind="ACTION", operation="http.delete", tool="http_post",
                              params={"url": "https://demo.svc.internal/x"})])
    result = h.controller.run(h.spec())
    assert h.runs == []
    assert result.stop_reason == "out_of_scope"


def test_replay_does_not_resend() -> None:
    h = Harness([_action(), _finish()])
    h.controller.run(h.spec())
    h.controller.run(h.spec())  # повторный прогон тех же шагов (детерминированные id)
    assert len(h.runs) == 1  # физическая отправка не повторяется


def test_outcome_and_stop_reason_separate_on_limit() -> None:
    # Модель всё время просит ещё данных -> предел шагов, исход INCONCLUSIVE.
    h = Harness([StepDecision(kind="NEED_EVIDENCE")])
    result = h.controller.run(h.spec(max_steps=3))
    assert result.outcome is EpisodeOutcome.INCONCLUSIVE
    assert result.stop_reason == "step_limit"
    assert result.steps_taken == 3


def test_durable_close_is_idempotent() -> None:
    h = Harness([_finish()])
    campaign = CampaignController()
    campaign.open_episode(h.scope, "ep-1")
    r1 = campaign.accept_closed("ep-1", EpisodeOutcome.SUPPORTED, "finished")
    r2 = campaign.accept_closed("ep-1", EpisodeOutcome.REFUTED, "other")
    assert r1.accepted is True
    assert r2.accepted is False  # повтор не применяется второй раз
    assert r2.outcome is EpisodeOutcome.SUPPORTED  # возвращается прежний итог


def test_local_engine_runs_episode() -> None:
    h = Harness([_action(), _finish()])
    engine = LocalExecutionEngine()
    result = engine.run(h.controller, h.spec())
    assert result.outcome is EpisodeOutcome.SUPPORTED
