"""Контракт capability research-agent."""

from __future__ import annotations

from decimal import Decimal

from attacking_agent.domain.budget import (
    BudgetAccountRef,
    InMemoryBudgetStore,
    LedgerManager,
)
from attacking_agent.domain.common.clock import ManualClock
from attacking_agent.domain.common.ids import SequentialIdFactory
from attacking_agent.domain.config import FrozenCampaignConfig
from attacking_agent.domain.llm import LLMProxy, LLMRequest, LLMResponse, Usage
from attacking_agent.domain.research import DraftStatus, ResearchAgent, ResearchInput


def _proposal(secret_leak: bool = False) -> dict:
    auth = {"password": "hunter2"} if secret_leak else {"ref": "vault://demo/token"}
    return {
        "objective": {
            "zone": "Форма обратной связи",
            "observation_criteria": ["ответ 200 с отражением"],
            "uncertainty": "неизвестна нейтрализация",
        },
        "interfaces": [
            {
                "name": "public-http",
                "kind": "http",
                "address": "https://demo.svc.internal/feedback",
                "capability_refs": ["submit"],
                "auth_ref": auth,
            }
        ],
        "budgets": [
            {"budget_type": "requests", "level": "campaign", "hardness": "hard", "limit": 50}
        ],
        "policy": {
            "policy_version": "p1",
            "allowed_operations": ["http.post"],
            "allowed_addresses": ["https://demo.svc.internal/*"],
        },
    }


class _ScriptedLLM:
    def __init__(self, proposal: dict) -> None:
        self._proposal = proposal
        self.calls = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            content="",
            structured=self._proposal,
            usage=Usage(prompt_tokens=50, completion_tokens=40),
            cost=Decimal("0.01"),
        )


def _agent(
    proposal: dict,
) -> tuple[ResearchAgent, LedgerManager, BudgetAccountRef, BudgetAccountRef]:
    mgr = LedgerManager(
        InMemoryBudgetStore(), clock=ManualClock(), id_factory=SequentialIdFactory()
    )
    admin = BudgetAccountRef("admin", BudgetLevel_CAMPAIGN(), "admin", MONEY())
    campaign = BudgetAccountRef("camp-1", BudgetLevel_CAMPAIGN(), "camp-1", MONEY())
    mgr.open_account(admin, limit=Decimal("1"), hardness=HARD(), currency="USD")
    mgr.open_account(campaign, limit=Decimal("5"), hardness=HARD(), currency="USD")
    proxy = LLMProxy(client=_ScriptedLLM(proposal), ledger=mgr)
    agent = ResearchAgent(proxy=proxy, admin_account=admin, admin_reserve=Decimal("0.1"))
    return agent, mgr, admin, campaign


# хелперы enum (чтобы не тащить длинные импорты в сигнатуры)
def BudgetLevel_CAMPAIGN():
    from attacking_agent.domain.config import BudgetLevel

    return BudgetLevel.CAMPAIGN


def MONEY():
    from attacking_agent.domain.config import BudgetType

    return BudgetType.MONEY


def HARD():
    from attacking_agent.domain.config import Hardness

    return Hardness.HARD


def _input() -> ResearchInput:
    return ResearchInput(
        target_ref="svc-demo",
        version="1.0.0",
        env_binding="staging",
        notes="публичная форма",
        admin_refs={"admin_credentials": {"ref": "vault://demo/admin"}},
    )


def test_valid_proposal_yields_draft() -> None:
    agent = _agent(_proposal())[0]
    res = agent.draft_kik(_input(), op_id="op-1", attempt_id="att-1", reservation_id="r1")
    assert res.status is DraftStatus.OK
    assert res.draft is not None
    assert res.draft.objective.zone.startswith("Форма")


def test_raw_secret_in_proposal_rejected() -> None:
    agent = _agent(_proposal(secret_leak=True))[0]
    res = agent.draft_kik(_input(), op_id="op-1", attempt_id="att-1", reservation_id="r1")
    assert res.status is DraftStatus.REJECTED
    assert res.draft is None


def test_uses_admin_budget_not_campaign() -> None:
    agent, mgr, admin, campaign = _agent(_proposal())
    agent.draft_kik(_input(), op_id="op-1", attempt_id="att-1", reservation_id="r1")
    assert mgr.consumed(admin) == Decimal("0.01")  # расход на admin-счёте
    assert mgr.consumed(campaign) == Decimal("0")  # бюджет кампании не тронут


def test_approve_and_freeze_and_private_not_in_projection() -> None:
    agent = _agent(_proposal())[0]
    res = agent.draft_kik(_input(), op_id="op-1", attempt_id="att-1", reservation_id="r1")
    assert res.draft is not None
    frozen = agent.approve_and_freeze(res.draft)
    assert isinstance(frozen, FrozenCampaignConfig)
    assert frozen.config_hash  # заморожена с хэшем
    text = str(frozen.public_projection().model_dump())
    assert "admin_credentials" not in text
    assert "vault://" not in text
