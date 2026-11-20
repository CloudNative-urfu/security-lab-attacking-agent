"""Библиотека стратегий атаки: курируемые техники с обучением по исходу.

Harness-сторона интеллекта: версионные стратегии (техника, предпосылки, ожидаемый эффект,
применимость, статистика успеха) с фильтром области и ранжированием по запросу. Модель
остаётся подключаемой; библиотека даёт планировщику опору на накопленный опыт.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

from pydantic import BaseModel, Field

from attacking_agent.domain.common.ids import IdFactory, UuidIdFactory
from attacking_agent.domain.common.scope import Scope
from attacking_agent.domain.memory.retrieval import RelevanceScorer, TokenOverlapScorer


class StrategySpec(BaseModel):
    technique: str = Field(min_length=1)
    preconditions: list[str] = Field(default_factory=list)
    expected_effect: str = ""
    applicability: str = ""
    grounds: list[str] = Field(default_factory=list)
    limits: list[str] = Field(default_factory=list)

    def searchable_text(self) -> str:
        parts = [self.technique, self.expected_effect, self.applicability]
        parts.extend(self.preconditions)
        return " ".join(p for p in parts if p)


@dataclass(frozen=True, slots=True)
class StrategyRecord:
    strategy_id: str
    version: int
    scope_key: str
    spec: StrategySpec
    times_tried: int = 0
    times_supported: int = 0

    @property
    def success_rate(self) -> float:
        return self.times_supported / self.times_tried if self.times_tried else 0.0


def _scope_key(scope: Scope) -> str:
    return f"{scope.campaign_id}:{scope.branch_id}:{scope.target_ref}"


class StrategyStore(Protocol):
    def add(self, record: StrategyRecord) -> None: ...
    def get(self, strategy_id: str) -> StrategyRecord | None: ...
    def update(self, record: StrategyRecord) -> None: ...
    def list_for_branch(self, scope_key: str) -> list[StrategyRecord]: ...


class InMemoryStrategyStore:
    def __init__(self) -> None:
        self._by_id: dict[str, StrategyRecord] = {}

    def add(self, record: StrategyRecord) -> None:
        self._by_id[record.strategy_id] = record

    def get(self, strategy_id: str) -> StrategyRecord | None:
        return self._by_id.get(strategy_id)

    def update(self, record: StrategyRecord) -> None:
        self._by_id[record.strategy_id] = record

    def list_for_branch(self, scope_key: str) -> list[StrategyRecord]:
        return [r for r in self._by_id.values() if r.scope_key == scope_key]


class StrategyLibrary:
    def __init__(
        self,
        store: StrategyStore,
        id_factory: IdFactory | None = None,
        scorer: RelevanceScorer | None = None,
    ) -> None:
        self._store = store
        self._ids = id_factory or UuidIdFactory()
        self._scorer = scorer or TokenOverlapScorer()

    def record(self, scope: Scope, spec: StrategySpec) -> StrategyRecord:
        rec = StrategyRecord(
            strategy_id=self._ids.new_id("strat"),
            version=1,
            scope_key=_scope_key(scope),
            spec=spec,
        )
        self._store.add(rec)
        return rec

    def retrieve(self, scope: Scope, query: str, limit: int) -> list[StrategyRecord]:
        candidates = self._store.list_for_branch(_scope_key(scope))
        scored = [
            (r, self._scorer.score(query, r.spec.searchable_text())) for r in candidates
        ]
        matched = [(r, s) for r, s in scored if s > 0.0]
        # ранжирование: релевантность, затем доля успеха (тай-брейк)
        matched.sort(key=lambda rs: (rs[1], rs[0].success_rate), reverse=True)
        return [r for r, _ in matched[:limit]]

    def note_outcome(self, scope: Scope, strategy_id: str, supported: bool) -> None:
        rec = self._store.get(strategy_id)
        if rec is None or rec.scope_key != _scope_key(scope):
            return
        self._store.update(
            replace(
                rec,
                times_tried=rec.times_tried + 1,
                times_supported=rec.times_supported + (1 if supported else 0),
            )
        )
