"""ORM-модели SQLAlchemy для источника истины (бюджет, журнал).

Денежные и количественные величины хранятся как текст (:class:`DecimalText`), чтобы
сохранить точность Decimal на любом бэкенде (включая SQLite в тестах и PostgreSQL в
production). Схема переносима между SQLite и PostgreSQL.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Float, Integer, String, Text, TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


class DecimalText(TypeDecorator[Decimal]):
    """Хранит Decimal как текст — точность не теряется ни на одном бэкенде."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect: Any) -> str | None:
        return None if value is None else str(value)

    def process_result_value(self, value: str | None, dialect: Any) -> Decimal | None:
        return None if value is None else Decimal(value)


class BudgetAccountRow(Base):
    __tablename__ = "budget_accounts"

    account_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String(128))
    level: Mapped[str] = mapped_column(String(32))
    scope_key: Mapped[str] = mapped_column(String(256))
    budget_type: Mapped[str] = mapped_column(String(32))
    limit_amount: Mapped[Decimal] = mapped_column(DecimalText)
    hardness: Mapped[str] = mapped_column(String(16))
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    consumed: Mapped[Decimal] = mapped_column(DecimalText)
    status: Mapped[str] = mapped_column(String(32))


class ReservationRow(Base):
    __tablename__ = "budget_reservations"

    reservation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    account_key: Mapped[str] = mapped_column(String(512), index=True)
    amount: Mapped[Decimal] = mapped_column(DecimalText)
    state: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[float] = mapped_column(Float)
    attempt_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    permit_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


class ChargeRow(Base):
    __tablename__ = "budget_charges"

    attempt_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    charge_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    actual: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)
    status: Mapped[str] = mapped_column(String(32))


class KnowledgeRow(Base):
    __tablename__ = "memory_knowledge"

    node_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    branch_key: Mapped[str] = mapped_column(String(256), index=True)
    version: Mapped[int] = mapped_column(Integer)
    statement: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32))
    conditions: Mapped[list[Any]] = mapped_column(JSON)
    evidence_refs: Mapped[list[Any]] = mapped_column(JSON)
    counterexamples: Mapped[list[Any]] = mapped_column(JSON)
    applicability: Mapped[str] = mapped_column(Text, default="")
    superseded: Mapped[bool] = mapped_column(Boolean, default=False)


class EventRow(Base):
    __tablename__ = "memory_events"

    branch_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    hash: Mapped[str] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    episode_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    op_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


class BranchVersionRow(Base):
    __tablename__ = "memory_branch_versions"

    branch_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=0)


class CommittedCandidateRow(Base):
    __tablename__ = "memory_committed_candidates"

    candidate_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    receipt_id: Mapped[str] = mapped_column(String(128))


class LLMAttemptRow(Base):
    __tablename__ = "llm_attempts"

    op_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    recorded: Mapped[bool] = mapped_column(Boolean, default=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)


class IntentRow(Base):
    __tablename__ = "journal_intents"

    op_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    branch_id: Mapped[str] = mapped_column(String(128), index=True)
    tool_name: Mapped[str] = mapped_column(String(128))
    permit_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


class ResultRow(Base):
    __tablename__ = "journal_results"

    op_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    branch_id: Mapped[str] = mapped_column(String(128), index=True)
    obs_status: Mapped[str] = mapped_column(String(32))
    obs_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    obs_subrequests: Mapped[int] = mapped_column(Integer)
    obs_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    dispatch_status: Mapped[str] = mapped_column(String(32))
    reconciled: Mapped[bool] = mapped_column(Boolean, default=False)
    final_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
