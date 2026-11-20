"""SQLAlchemy-реализация порта :class:`CallJournal` (durable журнал вызовов)."""

from __future__ import annotations

from sqlalchemy import Engine, select
from sqlalchemy.orm import sessionmaker

from attacking_agent.domain.tools import (
    IntentRecord,
    Observation,
    ObservationStatus,
    ResultRecord,
)

from .models import IntentRow, ResultRow


def _to_result(row: ResultRow) -> ResultRecord:
    return ResultRecord(
        op_id=row.op_id,
        attempt_id=row.attempt_id,
        branch_id=row.branch_id,
        observation=Observation(
            status=ObservationStatus(row.obs_status),
            payload=dict(row.obs_payload),
            subrequests=row.obs_subrequests,
            error=row.obs_error,
        ),
        dispatch_status=row.dispatch_status,
        reconciled=row.reconciled,
        final_status=ObservationStatus(row.final_status) if row.final_status else None,
    )


class SQLAlchemyCallJournal:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session_factory = sessionmaker(engine)

    def claim(self, intent: IntentRecord) -> bool:
        """Атомарно застолбить слот через вставку с конфликтом по первичному ключу."""
        from sqlalchemy.exc import IntegrityError

        try:
            with self._session_factory() as session, session.begin():
                session.add(
                    IntentRow(
                        op_id=intent.op_id,
                        attempt_id=intent.attempt_id,
                        branch_id=intent.branch_id,
                        tool_name=intent.tool_name,
                        permit_id=intent.permit_id,
                    )
                )
        except IntegrityError:
            return False
        return True

    def record_intent(self, intent: IntentRecord) -> None:
        with self._session_factory() as session, session.begin():
            if session.get(IntentRow, (intent.op_id, intent.attempt_id)) is not None:
                return
            session.add(
                IntentRow(
                    op_id=intent.op_id,
                    attempt_id=intent.attempt_id,
                    branch_id=intent.branch_id,
                    tool_name=intent.tool_name,
                    permit_id=intent.permit_id,
                )
            )

    def has_intent(self, op_id: str, attempt_id: str) -> bool:
        with self._session_factory() as session:
            return session.get(IntentRow, (op_id, attempt_id)) is not None

    def record_result(self, result: ResultRecord) -> None:
        with self._session_factory() as session, session.begin():
            if session.get(ResultRow, (result.op_id, result.attempt_id)) is not None:
                return
            session.add(
                ResultRow(
                    op_id=result.op_id,
                    attempt_id=result.attempt_id,
                    branch_id=result.branch_id,
                    obs_status=result.observation.status.value,
                    obs_payload=dict(result.observation.payload),
                    obs_subrequests=result.observation.subrequests,
                    obs_error=result.observation.error,
                    dispatch_status=result.dispatch_status,
                    reconciled=result.reconciled,
                    final_status=(
                        result.final_status.value if result.final_status else None
                    ),
                )
            )

    def get_result(self, op_id: str, attempt_id: str) -> ResultRecord | None:
        with self._session_factory() as session:
            row = session.get(ResultRow, (op_id, attempt_id))
            return _to_result(row) if row is not None else None

    def reconcile(self, op_id: str, attempt_id: str, final_status: ObservationStatus) -> None:
        with self._session_factory() as session, session.begin():
            row = session.get(ResultRow, (op_id, attempt_id))
            if row is not None:
                row.reconciled = True
                row.final_status = final_status.value

    def unresolved_unknowns(self, branch_id: str) -> list[ResultRecord]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(ResultRow).where(
                    ResultRow.branch_id == branch_id,
                    ResultRow.obs_status == ObservationStatus.UNKNOWN.value,
                    ResultRow.reconciled.is_(False),
                )
            )
            return [_to_result(r) for r in rows]
