"""Capability state-checkpoint: полный снимок АГ+СС, возобновление и форк."""

from attacking_agent.domain.checkpoint.manager import (
    CheckpointManager,
    CheckpointResult,
    CheckpointStatus,
    Manifest,
)
from attacking_agent.domain.checkpoint.stores import (
    ArtifactStore,
    InMemoryManifestStore,
    LocalArtifactStore,
    ManifestStore,
    StaticTargetSnapshotAdapter,
    TargetSnapshotAdapter,
)

__all__ = [
    "ArtifactStore",
    "CheckpointManager",
    "CheckpointResult",
    "CheckpointStatus",
    "InMemoryManifestStore",
    "LocalArtifactStore",
    "Manifest",
    "ManifestStore",
    "StaticTargetSnapshotAdapter",
    "TargetSnapshotAdapter",
]
