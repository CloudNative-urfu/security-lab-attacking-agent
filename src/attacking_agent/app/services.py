"""Сервисы операторского API: команды, read-модели, авторизация.

Команды идут через контролируемый путь с идемпотентностью по command_id и записью в
outbox в одной транзакции. Read-модели отдают только проекции без секретов. Авторизация
вынесена в порт (OIDC-совместимый) и проверяется до выдачи данных.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class CommandStatus(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"


_ALLOWED_KINDS = frozenset({"start", "pause", "resume", "fork"})


def _content_hash(kind: str, payload: dict[str, Any]) -> str:
    blob = json.dumps({"kind": kind, "payload": payload}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CommandResult:
    status: CommandStatus
    workflow_ref: str


@dataclass(slots=True)
class _CommandRecord:
    content_hash: str
    workflow_ref: str


@dataclass(slots=True)
class OutboxEntry:
    command_id: str
    campaign_id: str
    kind: str
    payload: dict[str, Any]


class CommandService:
    """Идемпотентный приём команд с outbox (одна транзакция под lock)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[str, _CommandRecord] = {}
        self._outbox: list[OutboxEntry] = []

    def submit(
        self, campaign_id: str, command_id: str, kind: str, payload: dict[str, Any]
    ) -> CommandResult:
        if kind not in _ALLOWED_KINDS:
            raise ValueError(f"недопустимый вид команды: {kind}")
        chash = _content_hash(kind, payload)
        with self._lock:
            existing = self._records.get(command_id)
            if existing is not None:
                if existing.content_hash == chash:
                    return CommandResult(CommandStatus.DUPLICATE, existing.workflow_ref)
                return CommandResult(CommandStatus.CONFLICT, existing.workflow_ref)
            workflow_ref = f"wf-{command_id}"
            # доменное изменение и запись в outbox — в одной транзакции (под lock)
            self._records[command_id] = _CommandRecord(chash, workflow_ref)
            self._outbox.append(OutboxEntry(command_id, campaign_id, kind, payload))
            return CommandResult(CommandStatus.ACCEPTED, workflow_ref)

    @property
    def outbox(self) -> list[OutboxEntry]:
        return list(self._outbox)


# Множество ключей, которые нельзя экспортировать по умолчанию.
_SECRET_KEYS = ("auth_ref", "admin", "secret", "token", "password", "credential", "private")


def strip_secrets(value: Any) -> Any:
    """Рекурсивно удалить потенциально секретные поля и ref-значения из проекции."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            low = str(k).lower()
            if any(marker in low for marker in _SECRET_KEYS):
                continue
            out[str(k)] = strip_secrets(v)
        return out
    if isinstance(value, list):
        return [strip_secrets(v) for v in value]
    if isinstance(value, str) and "vault://" in value:
        return "<secret-ref-removed>"
    return value


class ReadModelService:
    """Проекции для чтения оператором."""

    def __init__(
        self,
        state: dict[str, dict[str, Any]] | None = None,
        metrics: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._state = state or {}
        self._metrics = metrics or {}

    def get_state(self, campaign_id: str) -> dict[str, Any] | None:
        return self._state.get(campaign_id)

    def get_metrics(self, campaign_id: str) -> dict[str, Any]:
        return self._metrics.get(campaign_id, {})

    def export(self, campaign_id: str) -> dict[str, Any] | None:
        state = self._state.get(campaign_id)
        if state is None:
            return None
        cleaned = strip_secrets(state)
        return cleaned if isinstance(cleaned, dict) else {}


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    roles: frozenset[str] = field(default_factory=frozenset)


class AuthProvider(Protocol):
    """Порт авторизации (OIDC-совместимый)."""

    def authenticate(self, token: str | None) -> Principal | None: ...


class StaticAuthProvider:
    """Простой провайдер для MVP/тестов: проверка по набору валидных токенов."""

    def __init__(self, valid_tokens: set[str]) -> None:
        self._valid = set(valid_tokens)

    def authenticate(self, token: str | None) -> Principal | None:
        if token is not None and token in self._valid:
            return Principal(subject=f"user:{token}", roles=frozenset({"operator"}))
        return None
