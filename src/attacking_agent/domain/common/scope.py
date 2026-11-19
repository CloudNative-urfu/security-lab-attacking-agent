"""Область (scope) — проверяемая граница доступа.

Scope определяется доверенной стороной из идентичности и сопровождает каждую
операцию чтения/записи памяти, резерва бюджета и допуска. Пул соединений или
кэш не должны переносить scope предыдущего запроса — поэтому scope передаётся явно.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from attacking_agent.domain.common.ids import BranchId, CampaignId


class Scope(BaseModel):
    """Неизменяемая область действия операции."""

    model_config = ConfigDict(frozen=True)

    campaign_id: CampaignId
    branch_id: BranchId
    target_ref: str
    """Идентичность исследуемого экземпляра СС (target binding), не просто URL."""

    def is_same_branch(self, other: Scope) -> bool:
        return (
            self.campaign_id == other.campaign_id
            and self.branch_id == other.branch_id
            and self.target_ref == other.target_ref
        )
