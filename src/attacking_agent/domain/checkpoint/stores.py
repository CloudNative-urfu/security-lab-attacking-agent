"""Порты хранилищ чекпоинта и их реализации.

ArtifactStore — content-addressed хранилище бинарных частей (локальная ФС для MVP,
S3/SeaweedFS за тем же портом). ManifestStore — публикация READY-head по command_id.
TargetSnapshotAdapter — снимок/восстановление/клон СС; при отсутствии полный чекпоинт
недопустим (BLOCKED).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol


class ArtifactStore(Protocol):
    """Порт content-addressed хранилища."""

    def put(self, data: bytes) -> str: ...
    def exists(self, content_hash: str) -> bool: ...
    def get(self, content_hash: str) -> bytes: ...


class LocalArtifactStore:
    """Локальное content-addressed хранилище на файловой системе."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, content_hash: str) -> Path:
        return self._root / content_hash

    def put(self, data: bytes) -> str:
        content_hash = hashlib.sha256(data).hexdigest()
        path = self._path(content_hash)
        if not path.exists():
            # запись через временный файл для атомарности
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
        return content_hash

    def exists(self, content_hash: str) -> bool:
        return self._path(content_hash).exists()

    def get(self, content_hash: str) -> bytes:
        return self._path(content_hash).read_bytes()


class TargetSnapshotAdapter(Protocol):
    """Порт снимка стороннего сервиса (СС)."""

    def snapshot(self, target_ref: str) -> str: ...
    def restore(self, snapshot_ref: str) -> None: ...
    def clone(self, snapshot_ref: str) -> str: ...


class StaticTargetSnapshotAdapter:
    """Заглушка адаптера снимка СС для тестовой среды (изолированный/stub target)."""

    def __init__(self) -> None:
        self._counter = 0

    def snapshot(self, target_ref: str) -> str:
        self._counter += 1
        return f"snap-{target_ref}-{self._counter}"

    def restore(self, snapshot_ref: str) -> None:
        return None

    def clone(self, snapshot_ref: str) -> str:
        self._counter += 1
        return f"clone-{snapshot_ref}-{self._counter}"


class ManifestStore(Protocol):
    """Порт публикации READY-манифеста по (scope, command_id)."""

    def get(self, scope_key: str, command_id: str) -> object | None: ...
    def put(self, scope_key: str, command_id: str, manifest: object) -> None: ...


class InMemoryManifestStore:
    """In-memory реализация :class:`ManifestStore`."""

    def __init__(self) -> None:
        self._manifests: dict[tuple[str, str], object] = {}

    def get(self, scope_key: str, command_id: str) -> object | None:
        return self._manifests.get((scope_key, command_id))

    def put(self, scope_key: str, command_id: str, manifest: object) -> None:
        self._manifests.setdefault((scope_key, command_id), manifest)
