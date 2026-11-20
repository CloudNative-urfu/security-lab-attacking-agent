"""pgvector-индекс знаний: производный ANN-поиск с ОБЯЗАТЕЛЬНЫМ фильтром области.

Векторный поиск — не источник истины, а производный способ. Фильтр по ветке (scope)
применяется в самом SQL-запросе (WHERE), поэтому ANN не может вернуть чужую ветку.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, text


def _vec_literal(embedding: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in embedding) + "]"


@dataclass(frozen=True, slots=True)
class VectorHit:
    node_id: str
    statement: str
    distance: float


class PgVectorKnowledgeIndex:
    def __init__(self, engine: Engine, dim: int) -> None:
        self._engine = engine
        self._dim = dim

    def ensure_schema(self) -> None:
        with self._engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS knowledge_vectors ("
                    "node_id text PRIMARY KEY, branch_key text NOT NULL, "
                    f"statement text NOT NULL, embedding vector({self._dim}) NOT NULL)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS knowledge_vectors_branch "
                    "ON knowledge_vectors (branch_key)"
                )
            )

    def index(
        self, branch_key: str, node_id: str, statement: str, embedding: list[float]
    ) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO knowledge_vectors(node_id, branch_key, statement, embedding) "
                    "VALUES (:n, :b, :s, cast(:e as vector)) "
                    "ON CONFLICT (node_id) DO UPDATE SET "
                    "branch_key=:b, statement=:s, embedding=cast(:e as vector)"
                ),
                {"n": node_id, "b": branch_key, "s": statement, "e": _vec_literal(embedding)},
            )

    def search(
        self, branch_key: str, query_embedding: list[float], limit: int
    ) -> list[VectorHit]:
        # Фильтр области (branch_key) обязателен и применяется в SQL — чужая ветка невозможна.
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT node_id, statement, (embedding <=> cast(:q as vector)) AS dist "
                    "FROM knowledge_vectors WHERE branch_key = :b "
                    "ORDER BY dist ASC LIMIT :k"
                ),
                {"q": _vec_literal(query_embedding), "b": branch_key, "k": limit},
            ).all()
        return [VectorHit(node_id=r[0], statement=r[1], distance=float(r[2])) for r in rows]
