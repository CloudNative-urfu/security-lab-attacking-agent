"""Исследующий агент: подготовка черновика КИК по стороннему сервису.

Отдельный привилегированный контекст подготовки. Он НЕ исполняет инструменты против СС и
НЕ расширяет полномочия атакующего: LLM лишь предлагает конфигурацию, которую агент
валидирует той же проверкой, что и КИК (включая запрет сырых секретов), после чего оператор
утверждает черновик (freeze). Подготовительные вызовы идут под отдельным admin-бюджетом.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from attacking_agent.domain.budget import BudgetAccountRef
from attacking_agent.domain.common.errors import SecretLeakError, ValidationError
from attacking_agent.domain.config import CampaignConfig, FrozenCampaignConfig
from attacking_agent.domain.llm import LLMProxy, LLMRequest, ProxyStatus


class DraftStatus(StrEnum):
    OK = "ok"
    INVALID = "invalid"  # проблема на стороне модели/допуска
    REJECTED = "rejected"  # предложение не прошло валидацию КИК
    NO_BUDGET = "no_budget"


class KikProposal(BaseModel):
    """Структурированное предложение секций КИК от модели."""

    objective: dict[str, Any]
    interfaces: list[dict[str, Any]]
    budgets: list[dict[str, Any]]
    policy: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ResearchInput:
    target_ref: str
    version: str
    env_binding: str
    notes: str = ""
    # Приватные ссылки (admin), добавляемые оператором, НЕ моделью.
    admin_refs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DraftResult:
    status: DraftStatus
    draft: CampaignConfig | None = None
    reason: str = ""


class ResearchAgent:
    def __init__(
        self,
        proxy: LLMProxy,
        admin_account: BudgetAccountRef,
        admin_reserve: Decimal,
    ) -> None:
        self._proxy = proxy
        self._admin_account = admin_account
        self._admin_reserve = admin_reserve

    def draft_kik(
        self, research: ResearchInput, op_id: str, attempt_id: str, reservation_id: str
    ) -> DraftResult:
        req = LLMRequest(
            op_id=op_id,
            attempt_id=attempt_id,
            initiator="research-agent",
            model_ref="model-x",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Предложи КИК для авторизованного исследования СС: разделы "
                        "objective, interfaces, budgets, policy. Секреты — только как "
                        "{\"ref\": ...}, никогда сырыми значениями. Не выходи за рамки "
                        "публично допустимых интерфейсов."
                    ),
                },
                {"role": "user", "content": f"Цель: {research.target_ref}; {research.notes}"},
            ],
            max_tokens=512,
        )
        result = self._proxy.request(
            req,
            account_ref=self._admin_account,
            reserve_amount=self._admin_reserve,
            reservation_id=reservation_id,
            schema=KikProposal,
        )
        if result.status is ProxyStatus.NO_BUDGET:
            return DraftResult(DraftStatus.NO_BUDGET, reason="нет admin-бюджета")
        if result.status is not ProxyStatus.OK or not isinstance(result.structured, KikProposal):
            return DraftResult(DraftStatus.INVALID, reason=result.status.value)

        proposal = result.structured
        raw = {
            "objective": proposal.objective,
            "target": {
                "target_ref": research.target_ref,
                "version": research.version,
                "env_binding": research.env_binding,
            },
            "interfaces": proposal.interfaces,
            "budgets": proposal.budgets,
            "policy": proposal.policy,
            "memory_state": {"start_checkpoint_ref": None, "full_restore_required": True},
            "private_refs": dict(research.admin_refs),
        }
        try:
            draft = CampaignConfig.from_raw(raw)
        except (SecretLeakError, ValidationError) as exc:
            return DraftResult(DraftStatus.REJECTED, reason=str(exc))
        return DraftResult(DraftStatus.OK, draft)

    def approve_and_freeze(self, draft: CampaignConfig) -> FrozenCampaignConfig:
        """Утверждение оператором: заморозить черновик в неизменяемую КИК."""
        return draft.freeze()
