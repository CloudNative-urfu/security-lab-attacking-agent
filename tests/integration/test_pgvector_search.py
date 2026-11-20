"""Доказательство: pgvector ANN-поиск на реальном PG с обязательным фильтром области."""

from __future__ import annotations

import uuid

import pytest

from attacking_agent.adapters.pgvector_search import PgVectorKnowledgeIndex
from attacking_agent.domain.memory.retrieval import HashingEmbedder

pytestmark = pytest.mark.integration


def test_pgvector_ann_ranks_and_enforces_scope(pg_url: str) -> None:
    from sqlalchemy import create_engine

    engine = create_engine(pg_url)
    emb = HashingEmbedder(dim=64)
    index = PgVectorKnowledgeIndex(engine, dim=emb.dim)
    index.ensure_schema()

    br_a = f"camp:{uuid.uuid4().hex[:8]}:a"
    br_b = f"camp:{uuid.uuid4().hex[:8]}:b"

    docs_a = {
        "n1": "форма обратной связи отражает ввод пользователя",
        "n2": "таймаут сети на эндпойнте оплаты",
    }
    for nid, stmt in docs_a.items():
        index.index(br_a, nid, stmt, emb.embed([stmt])[0])
    # такая же релевантная запись, но в ЧУЖОЙ ветке
    index.index(br_b, "nb", "форма обратной связи отражает ввод пользователя",
                emb.embed(["форма обратной связи отражает ввод пользователя"])[0])

    qvec = emb.embed(["форма ввод отражение"])[0]
    hits = index.search(br_a, qvec, limit=10)

    ids = [h.node_id for h in hits]
    assert ids, "ожидались результаты в своей ветке"
    assert ids[0] == "n1"  # наиболее релевантная запись — первой
    assert "nb" not in ids  # чужая ветка НЕ возвращается ANN (фильтр области в SQL)

    engine.dispose()
