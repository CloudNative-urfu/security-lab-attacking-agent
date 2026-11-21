"""Отказ модели — штатный исход эпизода (guardrail сработал), а не падение пайплайна."""

from __future__ import annotations

from decimal import Decimal

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
    EpisodeOutcome,
    EpisodeSpec,
    MetricsControl,
)
from attacking_agent.domain.policy import PolicyEnforcer, StaticAddressResolver
from attacking_agent.domain.tools import (
    Dispatcher,
    InMemoryCallJournal,
    LocalToolRunner,
    ToolRegistry,
)

REFUSAL = "Я не могу выполнять подобные действия."


class _RefusingLLM:
    """Модель отвечает прозой без решения по схеме (как реальный отказ aligned-модели)."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            content=REFUSAL, structured=None,
            usage=Usage(prompt_tokens=5, completion_tokens=5), cost=Decimal("0.001"),
        )


def _controller(mem: InMemoryMemoryStore, metrics: MetricsControl, llm: _RefusingLLM):
    mgr = LedgerManager(
        InMemoryBudgetStore(), clock=ManualClock(), id_factory=SequentialIdFactory()
    )
    money = BudgetAccountRef("c", BudgetLevel.CAMPAIGN, "c", BudgetType.MONEY)
    reqs = BudgetAccountRef("c", BudgetLevel.CAMPAIGN, "c", BudgetType.REQUESTS)
    mgr.open_account(money, limit=Decimal("1"), hardness=Hardness.HARD, currency="USD")
    mgr.open_account(reqs, limit=Decimal("50"), hardness=Hardness.HARD)
    policy = PolicyEnforcer(
        PolicySpec(policy_version="p1", allowed_operations=["http.post"],
                   allowed_addresses=["https://demo.svc.internal/*"]),
        resolver=StaticAddressResolver({"demo.svc.internal": ["203.0.113.10"]}),
    )
    dispatcher = Dispatcher(policy=policy, ledger=mgr, journal=InMemoryCallJournal(),
                            runner=LocalToolRunner(), registry=ToolRegistry())
    return EpisodeController(
        reader=Reader(mem), writer=Writer(mem, id_factory=SequentialIdFactory()),
        proxy=LLMProxy(client=llm, ledger=mgr), dispatcher=dispatcher, policy=policy,
        metrics=metrics, llm_account=money, tool_account=reqs,
        llm_reserve=Decimal("0.05"), tool_reserve=Decimal("1"),
    )


def test_refusal_is_graceful_terminal_outcome() -> None:
    mem = InMemoryMemoryStore()
    metrics = MetricsControl()
    llm = _RefusingLLM()
    scope = Scope(campaign_id="c", branch_id="b", target_ref="t")

    result = _controller(mem, metrics, llm).run(
        EpisodeSpec("ep-r", scope, "вопрос", max_steps=5)
    )

    # штатное завершение, а не крах и не исчерпание лимита шагов
    assert result.stop_reason == "model_refused"
    assert result.outcome is EpisodeOutcome.INCONCLUSIVE
    assert result.steps_taken == 1
    assert llm.calls == 1  # остановились на первом отказе, не долбим модель весь лимит

    # отказ зафиксирован как измеримый сигнал guardrail
    assert len(metrics.refusals) == 1
    assert REFUSAL in str(metrics.refusals[0]["raw"])
