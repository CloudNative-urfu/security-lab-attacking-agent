"""Фикстуры интеграционных тестов на реальном PostgreSQL."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text

PG_URL = os.environ.get("AG_PG_URL", "postgresql+psycopg://ag:ag@localhost:5433/ag")


def _reachable(url: str) -> bool:
    try:
        eng = create_engine(url)
        with eng.connect() as conn:
            conn.execute(text("select 1"))
        eng.dispose()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def pg_url() -> str:
    if not _reachable(PG_URL):
        pytest.skip(f"PostgreSQL недоступен по {PG_URL} (поднимите docker compose up -d postgres)")
    return PG_URL
