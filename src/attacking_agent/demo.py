"""Демонстрационный сквозной прогон харнеса на in-memory адаптерах (офлайн).

Запуск: ``uv run python -m attacking_agent.demo``

Демо собирает КИК, запускает один эпизод авторизованного исследования против локальной
заглушки «стороннего сервиса» (никакой реальной сети), затем печатает итог эпизода,
журнал вызовов, расход бюджета и опубликованное в память знание. Это иллюстрирует
инварианты харнеса (допуск, учёт, журнал, память), а не реальную атаку.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from attacking_agent.domain.budget import (
    BudgetAccountRef,
    InMemoryBudgetStore,
    LedgerManager,
)
from attacking_agent.domain.common.clock import ManualClock
from attacking_agent.domain.common.ids import BranchId, CampaignId, SequentialIdFactory
from attacking_agent.domain.common.scope import Scope
from attacking_agent.domain.config import BudgetLevel, BudgetType, CampaignConfig, Hardness
from attacking_agent.domain.llm import LLMProxy, LLMRequest, LLMResponse, Usage
from attacking_agent.domain.memory import (
    InMemoryMemoryStore,
    KnowledgeInput,
    Reader,
    ReadMode,
    Writer,
)
from attacking_agent.domain.orchestration import (
    CampaignController,
    EpisodeController,
    EpisodeSpec,
    LocalExecutionEngine,
    MetricsControl,
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


class _FeedbackParams(BaseModel):
    url: str
    body: str = ""


def _build_kik() -> CampaignConfig:
    return CampaignConfig.from_raw(
        {
            "objective": {
                "zone": "Форма обратной связи демонстрационного портала",
                "observation_criteria": ["ответ 200 с отражённым вводом"],
                "uncertainty": "неизвестно наличие серверной нейтрализации ввода",
            },
            "target": {
                "target_ref": "svc-demo",
                "version": "1.0.0",
                "env_binding": "staging-local",
            },
            "interfaces": [
                {
                    "name": "public-http",
                    "kind": "http",
                    "address": "https://demo.svc.internal/feedback",
                    "capability_refs": ["submit-feedback"],
                    "auth_ref": {"ref": "vault://demo/feedback-token"},
                }
            ],
            "budgets": [
                {"budget_type": "requests", "level": "campaign", "hardness": "hard", "limit": 20},
                {
                    "budget_type": "money",
                    "level": "campaign",
                    "hardness": "hard",
                    "limit": "1.00",
                    "currency": "USD",
                },
            ],
            "policy": {
                "policy_version": "p1",
                "allowed_operations": ["http.post"],
                "allowed_addresses": ["https://demo.svc.internal/*"],
            },
            "memory_state": {"start_checkpoint_ref": None, "full_restore_required": True},
            "private_refs": {"admin_credentials": {"ref": "vault://demo/admin"}},
        }
    )


class _DemoPlanner:
    """Детерминированный «планировщик» вместо реальной LLM: ACTION → FINISH."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            decision = StepDecision(
                kind="ACTION",
                operation="http.post",
                tool="feedback_probe",
                params={"url": "https://demo.svc.internal/feedback", "body": "<probe-123>"},
                hypothesis="ввод отражается в ответе без нейтрализации",
            )
        else:
            decision = StepDecision(kind="FINISH", outcome="SUPPORTED")
        return LLMResponse(
            content="",
            structured=decision.model_dump(),
            usage=Usage(prompt_tokens=20, completion_tokens=10),
            cost=Decimal("0.002"),
        )


def _feedback_target(ctx: ToolContext, params: _FeedbackParams) -> dict[str, Any]:
    """Локальная заглушка СС: отражает ввод (исследуемое поведение), без реальной сети."""
    ctx.subrequest()
    return {"status_code": 200, "reflected": params.body}


def run_demo() -> dict[str, Any]:
    ids = SequentialIdFactory()
    scope = Scope(
        campaign_id=CampaignId("camp-demo"), branch_id=BranchId("main"), target_ref="svc-demo"
    )

    kik = _build_kik().freeze()

    # Бюджеты из КИК (здесь — вручную по двум счётам).
    ledger = LedgerManager(InMemoryBudgetStore(), clock=ManualClock(), id_factory=ids)
    requests_acc = BudgetAccountRef(
        "camp-demo", BudgetLevel.CAMPAIGN, "camp-demo", BudgetType.REQUESTS
    )
    money_acc = BudgetAccountRef(
        "camp-demo", BudgetLevel.CAMPAIGN, "camp-demo", BudgetType.MONEY
    )
    ledger.open_account(requests_acc, limit=Decimal("20"), hardness=Hardness.HARD)
    ledger.open_account(money_acc, limit=Decimal("1.00"), hardness=Hardness.HARD, currency="USD")

    policy = PolicyEnforcer(
        kik.policy,
        resolver=StaticAddressResolver({"demo.svc.internal": ["203.0.113.10"]}),
    )

    registry = ToolRegistry()
    registry.register(
        ToolContract(
            name="feedback_probe",
            operation="http.post",
            params_model=_FeedbackParams,
            executor=_feedback_target,
            address_field="url",
        )
    )
    journal = InMemoryCallJournal()
    dispatcher = Dispatcher(
        policy=policy,
        ledger=ledger,
        journal=journal,
        runner=LocalToolRunner(subrequest_cap=8),
        registry=registry,
    )

    mem = InMemoryMemoryStore()
    reader = Reader(mem)
    writer = Writer(mem)
    proxy = LLMProxy(client=_DemoPlanner(), ledger=ledger)
    metrics = MetricsControl()

    controller = EpisodeController(
        reader=reader,
        writer=writer,
        proxy=proxy,
        dispatcher=dispatcher,
        policy=policy,
        metrics=metrics,
        llm_account=money_acc,
        tool_account=requests_acc,
        llm_reserve=Decimal("0.05"),
        tool_reserve=Decimal("1"),
    )

    campaign = CampaignController()
    campaign.open_episode(scope, "ep-1")
    engine = LocalExecutionEngine()
    spec = EpisodeSpec("ep-1", scope, "есть ли отражение ввода", max_steps=5)
    result = engine.run(controller, spec)
    close = campaign.accept_closed("ep-1", result.outcome, result.stop_reason)

    # Публикация знания по итогу наблюдения (путь памяти Writer).
    base = mem.branch_version(scope)
    candidate = writer.prepare(
        scope,
        base_version=base,
        read_set=["ep-1-obs-0"],
        knowledge=[
            KnowledgeInput(
                statement="ввод отражается в ответе формы обратной связи без нейтрализации",
                status="confirmed",
                conditions=["эндпойнт /feedback", "метод POST"],
                evidence_refs=["ep-1-obs-0"],
            )
        ],
    )
    assessment = writer.evaluate(scope, candidate)
    receipt = writer.commit(scope, candidate, assessment)
    knowledge = reader.read(scope, ReadMode.TASK, query="отраж", limit=10)

    unknown = journal.unresolved_unknowns(scope.branch_id)
    return {
        "config_version": kik.config_version,
        "config_hash": kik.config_hash[:16] + "…",
        "episode_outcome": result.outcome.value,
        "stop_reason": result.stop_reason,
        "steps_taken": result.steps_taken,
        "closed_accepted": close.accepted,
        "tool_requests_consumed": str(ledger.consumed(requests_acc)),
        "llm_money_consumed_usd": str(ledger.consumed(money_acc)),
        "journal_result_recorded": journal.get_result("ep-1-op-0", "ep-1-att-0") is not None,
        "unresolved_unknowns": len(unknown),
        "memory_publish_status": receipt.status.value,
        "memory_known_statements": [k.statement for k in knowledge.knowledge],
    }


def main() -> None:
    summary = run_demo()
    print("=" * 72)
    print("ДЕМО ХАРНЕСА АТАКУЮЩЕГО АГЕНТА · сквозной прогон (офлайн, in-memory)")
    print("=" * 72)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("-" * 72)
    print("Инварианты: допуск до отправки, учёт расхода, durable журнал, память под scope.")


if __name__ == "__main__":
    main()
