"""Сущности памяти и входные модели.

Четыре типа: Events (append-only), Knowledge, Strategies, Edges. Knowledge и
Strategies версионны и несут условия, основания и контрпримеры; Edges — типизированные
связи с областью, версией и происхождением.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ReadMode(StrEnum):
    TASK = "task"
    ANALYSIS = "analysis"


class CandidateState(StrEnum):
    PREPARED = "prepared"
    EVALUATED = "evaluated"
    COMMITTED = "committed"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class EventInput(BaseModel):
    model_config = ConfigDict(frozen=True)
    event_id: str
    hash: str
    source: str
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    episode_id: str | None = None
    op_id: str | None = None


class KnowledgeInput(BaseModel):
    model_config = ConfigDict(frozen=True)
    statement: str
    status: str = "hypothesis"
    conditions: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    counterexamples: list[str] = Field(default_factory=list)
    applicability: str = ""


@dataclass(frozen=True, slots=True)
class Event:
    event_id: str
    hash: str
    scope_key: str
    source: str
    kind: str
    payload: dict[str, Any]
    episode_id: str | None
    op_id: str | None


@dataclass(frozen=True, slots=True)
class KnowledgeNode:
    node_id: str
    version: int
    scope_key: str
    statement: str
    status: str
    conditions: list[str]
    evidence_refs: list[str]
    counterexamples: list[str]
    applicability: str
    superseded: bool = False


@dataclass(frozen=True, slots=True)
class Edge:
    edge_id: str
    edge_type: str
    from_ref: str
    to_ref: str
    scope_key: str
    version: int
    origin: str


def composition_hash(
    scope_key: str, base_version: int, read_set: list[str], knowledge: list[KnowledgeInput]
) -> str:
    material = {
        "scope": scope_key,
        "base": base_version,
        "read_set": sorted(read_set),
        "knowledge": [k.model_dump() for k in knowledge],
    }
    blob = json.dumps(material, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Candidate:
    candidate_id: str
    scope_key: str
    base_version: int
    read_set: list[str]
    composition_hash: str
    knowledge: list[KnowledgeInput]
    state: CandidateState = CandidateState.PREPARED

    def with_tampered_composition(self) -> Candidate:
        """Вернуть копию с изменённым составом (для проверки аннулирования approval)."""
        return replace(self, composition_hash=self.composition_hash + "-tampered")


@dataclass(frozen=True, slots=True)
class Assessment:
    assessment_ref: str
    candidate_id: str
    composition_hash: str
    verdict: str


@dataclass(frozen=True, slots=True)
class CurationPlan:
    scope_key: str
    base_version: int
    read_set: list[str]
    operations: list[str] = field(default_factory=list)
