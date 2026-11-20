"""Интеграция стратегий в планировщик: доверенная секция контекста + обучение по исходу."""

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
    UNTRUSTED_OPEN,
    EpisodeController,
    EpisodeSpec,
    MetricsControl,
    StepDecision,
)
from attacking_agent.domain.policy import PolicyEnforcer, StaticAddressResolver
from attacking_agent.domain.strategy import (
    InMemoryStrategyStore,
    StrategyLibrary,
    StrategySpec,
)
from attacking_agent.domain.tools import (
    Dispatcher,
    InMemoryCallJournal,
    LocalToolRunner,
    ToolRegistry,
)


class _RecordingLLM:
    def __init__(self, decisions: list[StepDecision]) -> None:
        self._d = decisions
        self.calls = 0
        self.messages_seen: list[list[dict[str, str]]] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.messages_seen.append(list(request.messages))
        idx = min(self.calls, len(self._d) - 1)
        self.calls += 1
        return LLMResponse(
            content="", structured=self._d[idx].model_dump(),
            usage=Usage(prompt_tokens=5, completion_tokens=5), cost=Decimal("0.001"),
        )


def _controller(lib: StrategyLibrary | None):
    scope = Scope(campaign_id="c", branch_id="b", target_ref="t")
    mgr = LedgerManager(
        InMemoryBudgetStore(), clock=ManualClock(), id_factory=SequentialIdFactory()
    )
    money = BudgetAccountRef("c", BudgetLevel.CAMPAIGN, "c", BudgetType.MONEY)
    reqs = BudgetAccountRef("c", BudgetLevel.CAMPAIGN, "c", BudgetType.REQUESTS)
    mgr.open_account(money, limit=Decimal("1"), hardness=Hardness.HARD, currency="USD")
    mgr.open_account(reqs, limit=Decimal("50"), hardness=Hardness.HARD)
    policy = PolicyEnforcer(
        PolicySpec(policy_version="p1", allowed_operations=["http.post"], allowed_addresses=[]),
        resolver=StaticAddressResolver({}),
    )
    dispatcher = Dispatcher(
        policy=policy, ledger=mgr, journal=InMemoryCallJournal(),
        runner=LocalToolRunner(), registry=ToolRegistry(),
    )
    mem = InMemoryMemoryStore()
    llm = _RecordingLLM([StepDecision(kind="FINISH", outcome="SUPPORTED")])
    controller = EpisodeController(
        reader=Reader(mem), writer=Writer(mem, id_factory=SequentialIdFactory()),
        proxy=LLMProxy(client=llm, ledger=mgr), dispatcher=dispatcher, policy=policy,
        metrics=MetricsControl(), llm_account=money, tool_account=reqs,
        llm_reserve=Decimal("0.05"), tool_reserve=Decimal("1"), strategy_library=lib,
    )
    return controller, scope, llm


def test_strategies_injected_as_trusted_context_and_learned() -> None:
    store = InMemoryStrategyStore()
    lib = StrategyLibrary(store, id_factory=SequentialIdFactory())
    scope = Scope(campaign_id="c", branch_id="b", target_ref="t")
    rec = lib.record(scope, StrategySpec(technique="инъекция в поле обратной связи",
                                         expected_effect="отражение ввода"))

    controller, scope, llm = _controller(lib)
    result = controller.run(EpisodeSpec("ep-1", scope, "инъекция обратная связь", max_steps=2))

    user = llm.messages_seen[0][1]["content"]
    # стратегия в ДОВЕРЕННОЙ части (до блока недоверенных данных, если он есть)
    trusted = user.split(UNTRUSTED_OPEN, 1)[0]
    assert "инъекция в поле обратной связи" in trusted
    assert "Известные техники" in trusted
    # обучение: исход SUPPORTED -> засчитан успех стратегии
    updated = store.get(rec.strategy_id)
    assert updated is not None
    assert updated.times_tried == 1
    assert updated.times_supported == 1
    assert result.outcome.value == "SUPPORTED"


def test_without_library_no_strategy_section() -> None:
    controller, scope, llm = _controller(None)
    controller.run(EpisodeSpec("ep-1", scope, "вопрос", max_steps=2))
    user = llm.messages_seen[0][1]["content"]
    assert "Известные техники" not in user  # поведение прежнее
