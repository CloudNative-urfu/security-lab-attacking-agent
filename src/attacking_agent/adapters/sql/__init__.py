"""SQLAlchemy-адаптеры источника истины (бюджет, журнал).

Та же ORM работает на SQLite (MVP/тесты) и PostgreSQL (production) — меняется только
URL движка. Векторный поиск (pgvector) — отдельное production-расширение поверх этой схемы.
"""

from attacking_agent.adapters.sql.budget_store import SQLAlchemyBudgetStore, create_all
from attacking_agent.adapters.sql.journal import SQLAlchemyCallJournal
from attacking_agent.adapters.sql.llm_journal import SQLAlchemyLLMJournal
from attacking_agent.adapters.sql.memory_store import SQLAlchemyMemoryStore
from attacking_agent.adapters.sql.models import Base

__all__ = [
    "Base",
    "SQLAlchemyBudgetStore",
    "SQLAlchemyCallJournal",
    "SQLAlchemyLLMJournal",
    "SQLAlchemyMemoryStore",
    "create_all",
]
