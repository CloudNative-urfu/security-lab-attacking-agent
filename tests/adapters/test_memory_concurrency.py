"""Состязательный тест: кросс-процессный branch-fence памяти (разные соединения).

Два независимых воркера пытаются опубликовать под одной базой ветки. Корректная
БД-блокировка не допускает двойной публикации (версия продвигается ровно один раз).
"""

from __future__ import annotations

import threading
from pathlib import Path

from sqlalchemy import create_engine

from attacking_agent.adapters.sql import SQLAlchemyMemoryStore, create_all
from attacking_agent.domain.common.ids import UuidIdFactory
from attacking_agent.domain.common.scope import Scope
from attacking_agent.domain.memory import KnowledgeInput, Writer


def _scope() -> Scope:
    return Scope(campaign_id="c", branch_id="b", target_ref="t")


def _publish(db: Path, base: int, statement: str, out: list[str]) -> None:
    engine = create_engine(f"sqlite:///{db}")
    store = SQLAlchemyMemoryStore(engine)
    writer = Writer(store, id_factory=UuidIdFactory())
    cand = writer.prepare(_scope(), base_version=base, read_set=[],
                          knowledge=[KnowledgeInput(statement=statement)])
    res = writer.commit(_scope(), cand, writer.evaluate(_scope(), cand))
    out.append(res.status.value)
    engine.dispose()


def test_concurrent_publish_under_same_base_fences(tmp_path: Path) -> None:
    db = tmp_path / "mem.db"
    setup = create_engine(f"sqlite:///{db}")
    create_all(setup)
    setup.dispose()
    # Засеять версию 1 (строка версии ветки уже существует — коллизия PK не спасёт).
    _publish(db, 0, "знание-seed", [])

    results: list[str] = []
    barrier = threading.Barrier(2)

    def run(statement: str) -> None:
        barrier.wait()
        _publish(db, 1, statement, results)

    t1 = threading.Thread(target=run, args=("знание-A",))
    t2 = threading.Thread(target=run, args=("знание-B",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # под одной базой (1) публикуется ровно один кандидат; второй — VERSION_CONFLICT
    published = [s for s in results if s == "published"]
    assert len(published) == 1, results

    check = create_engine(f"sqlite:///{db}")
    store = SQLAlchemyMemoryStore(check)
    assert store.branch_version(_scope()) == 2  # версия продвинулась ровно один раз
    assert len(store.committed_knowledge(_scope())) == 2  # seed + один
    check.dispose()
