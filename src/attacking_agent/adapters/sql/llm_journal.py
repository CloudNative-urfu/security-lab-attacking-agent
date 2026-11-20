"""SQLAlchemy-реализация порта :class:`LLMJournal` (durable журнал попыток модели).

Делает гарантию «replay без переотправки» устойчивой к рестарту процесса, в отличие от
in-memory варианта. ``claim`` — атомарный захват слота через конфликт первичного ключа.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from attacking_agent.domain.llm import LLMResponse, Usage

from .models import LLMAttemptRow


class SQLAlchemyLLMJournal:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session_factory = sessionmaker(engine)

    def get(self, op_id: str, attempt_id: str) -> LLMResponse | None:
        with self._session_factory() as session:
            row = session.get(LLMAttemptRow, (op_id, attempt_id))
            if row is None or not row.recorded:
                return None
            return LLMResponse(
                content=row.content or "",
                structured=row.structured,
                usage=Usage(
                    prompt_tokens=row.prompt_tokens, completion_tokens=row.completion_tokens
                ),
                cost=row.cost if row.cost is not None else Decimal("0"),
            )

    def claim(self, op_id: str, attempt_id: str) -> bool:
        from sqlalchemy.exc import IntegrityError

        try:
            with self._session_factory() as session, session.begin():
                session.add(LLMAttemptRow(op_id=op_id, attempt_id=attempt_id, recorded=False))
        except IntegrityError:
            return False
        return True

    def record(self, op_id: str, attempt_id: str, response: LLMResponse) -> None:
        with self._session_factory() as session, session.begin():
            row = session.get(LLMAttemptRow, (op_id, attempt_id))
            if row is None:
                row = LLMAttemptRow(op_id=op_id, attempt_id=attempt_id)
                session.add(row)
            if row.recorded:
                return  # идемпотентно: первая запись не перезаписывается
            row.recorded = True
            row.content = response.content
            row.structured = response.structured
            row.prompt_tokens = response.usage.prompt_tokens
            row.completion_tokens = response.usage.completion_tokens
            row.cost = response.cost
