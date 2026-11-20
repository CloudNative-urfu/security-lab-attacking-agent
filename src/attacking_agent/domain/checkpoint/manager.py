"""Протокол полного чекпоинта АГ+СС, возобновления и форка.

Сначала барьер и сверка незавершённых операций (UNKNOWN блокирует READY), затем части
пишутся в content-addressed store, затем публикуется READY-манифест по command_id
(повтор команды возвращает прежний head, без дублирования). Полный чекпоинт требует
адаптера снимка СС; иначе BLOCKED. Форк наследует префикс истории, не видит будущее
родителя и не откатывает реальный расход.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum

from attacking_agent.domain.common.ids import BranchId, CampaignId, IdFactory, UuidIdFactory
from attacking_agent.domain.common.scope import Scope
from attacking_agent.domain.memory import KnowledgeNode, MemoryStore
from attacking_agent.domain.memory.store import scope_key
from attacking_agent.domain.tools import CallJournal

from .stores import ArtifactStore, ManifestStore, TargetSnapshotAdapter


class CheckpointStatus(StrEnum):
    READY = "ready"
    PENDING = "pending"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Manifest:
    checkpoint_id: str
    campaign_id: str
    branch_id: str
    target_ref: str
    command_id: str
    memory_version: int
    part_hashes: list[str]
    target_snapshot_ref: str | None
    knowledge_snapshot: list[KnowledgeNode] = field(default_factory=list)

    @property
    def scope_key(self) -> str:
        return f"{self.campaign_id}:{self.branch_id}:{self.target_ref}"


@dataclass(frozen=True, slots=True)
class CheckpointResult:
    status: CheckpointStatus
    manifest: Manifest | None = None
    reason: str = ""


class CheckpointManager:
    def __init__(
        self,
        artifact_store: ArtifactStore,
        manifest_store: ManifestStore,
        memory_store: MemoryStore,
        journal: CallJournal,
        snapshot_adapter: TargetSnapshotAdapter | None,
        id_factory: IdFactory | None = None,
    ) -> None:
        self._artifacts = artifact_store
        self._manifests = manifest_store
        self._memory = memory_store
        self._journal = journal
        self._snapshot = snapshot_adapter
        self._ids = id_factory or UuidIdFactory()

    def create_checkpoint(self, scope: Scope, command_id: str) -> CheckpointResult:
        key = scope_key(scope)

        # Идемпотентность по (scope, command_id): повтор команды -> прежний head.
        prior = self._manifests.get(key, command_id)
        if isinstance(prior, Manifest):
            return CheckpointResult(CheckpointStatus.READY, prior, "повтор команды")

        # Полный чекпоинт требует адаптера снимка СС.
        if self._snapshot is None:
            return CheckpointResult(
                CheckpointStatus.BLOCKED, reason="нет snapshot/restore/clone адаптера СС"
            )

        # Барьер/drain: незавершённый UNKNOWN не позволяет объявить READY.
        if self._journal.unresolved_unknowns(scope.branch_id):
            return CheckpointResult(
                CheckpointStatus.PENDING, reason="есть несверённые UNKNOWN-исходы"
            )

        # Снимок памяти в content-addressed store.
        nodes = self._memory.snapshot_knowledge(scope)
        blob = json.dumps(
            [n.statement for n in nodes], sort_keys=True, ensure_ascii=False
        ).encode("utf-8")
        part_hash = self._artifacts.put(blob)
        memory_version = self._memory.branch_version(scope)

        # Привилегированный снимок СС.
        target_snapshot_ref = self._snapshot.snapshot(scope.target_ref)

        manifest = Manifest(
            checkpoint_id=self._ids.new_id("ckpt"),
            campaign_id=scope.campaign_id,
            branch_id=scope.branch_id,
            target_ref=scope.target_ref,
            command_id=command_id,
            memory_version=memory_version,
            part_hashes=[part_hash],
            target_snapshot_ref=target_snapshot_ref,
            knowledge_snapshot=nodes,
        )
        self._manifests.put(key, command_id, manifest)
        return CheckpointResult(CheckpointStatus.READY, manifest)

    def fork(self, manifest: Manifest, new_branch_id: str) -> Scope:
        """Создать форк из исторической точки: независимая ветка и target binding."""
        fork_scope = Scope(
            campaign_id=CampaignId(manifest.campaign_id),
            branch_id=BranchId(new_branch_id),
            target_ref=manifest.target_ref,
        )
        self._memory.seed_branch(fork_scope, manifest.knowledge_snapshot)
        if self._snapshot is not None and manifest.target_snapshot_ref is not None:
            self._snapshot.clone(manifest.target_snapshot_ref)
        return fork_scope

    def resume(self, manifest: Manifest) -> Scope:
        """Продолжить последнюю точку: идентичность ветки сохраняется (новая epoch)."""
        resume_scope = Scope(
            campaign_id=CampaignId(manifest.campaign_id),
            branch_id=BranchId(manifest.branch_id),
            target_ref=manifest.target_ref,
        )
        if self._snapshot is not None and manifest.target_snapshot_ref is not None:
            self._snapshot.restore(manifest.target_snapshot_ref)
        return resume_scope
