"""Сервисы операторского API: команды, read-модели, авторизация.

Команды идут через контролируемый путь с идемпотентностью по command_id и записью в
outbox в одной транзакции. Read-модели отдают только проекции без секретов. Авторизация
вынесена в порт (OIDC-совместимый) и проверяется до выдачи данных.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from attacking_agent.domain.common.clock import Clock
from attacking_agent.domain.common.secrets_detect import (
    field_words,
    key_is_secret,
    looks_like_secret_value,
)


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
_REDACTED = "<secret-redacted>"
# Имена-слова, которые всегда вырезаются из экспорта (сверх общих маркеров).
_EXPORT_DROP_WORDS = frozenset({"auth", "admin"})


def strip_secrets(value: Any) -> Any:
    """Рекурсивно удалить секреты из проекции: по имени поля (границы слов), по значению
    (шаблоны секретов) и ref-значения (``vault://``)."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            words = field_words(str(k))
            if key_is_secret(str(k)) or (words & _EXPORT_DROP_WORDS):
                continue
            out[str(k)] = strip_secrets(v)
        return out
    if isinstance(value, list):
        return [strip_secrets(v) for v in value]
    if isinstance(value, str) and ("vault://" in value or looks_like_secret_value(value)):
        return _REDACTED
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
    scopes: frozenset[str] = field(default_factory=frozenset)

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


class AuthProvider(Protocol):
    """Порт авторизации (OIDC-совместимый)."""

    def authenticate(self, token: str | None) -> Principal | None: ...


_DEFAULT_SCOPES = frozenset({"campaign:read", "campaign:write"})


class StaticAuthProvider:
    """Простой провайдер для MVP/тестов: проверка по набору валидных токенов.

    Для хардёных сценариев с проверкой подписи/срока/области используйте
    :class:`SignedTokenAuthProvider`.
    """

    def __init__(
        self, valid_tokens: set[str], scopes: frozenset[str] = _DEFAULT_SCOPES
    ) -> None:
        self._valid = set(valid_tokens)
        self._scopes = scopes

    def authenticate(self, token: str | None) -> Principal | None:
        if token is not None and token in self._valid:
            return Principal(subject=f"user:{token}", scopes=self._scopes)
        return None


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def issue_token(secret_key: str, subject: str, scopes: list[str], exp_unix: int) -> str:
    """Выпустить HMAC-подписанный токен ``<payload>.<sig>`` (для ops/тестов)."""
    payload = json.dumps(
        {"sub": subject, "scopes": scopes, "exp": exp_unix}, sort_keys=True
    ).encode("utf-8")
    sig = hmac.new(secret_key.encode("utf-8"), payload, hashlib.sha256).digest()
    return f"{_b64url(payload)}.{_b64url(sig)}"


class SignedTokenAuthProvider:
    """Проверка целостности (HMAC), срока (``exp``) и области (``scopes``) токена."""

    def __init__(self, secret_key: str, clock: Clock | None = None) -> None:
        self._secret = secret_key.encode("utf-8")
        self._clock = clock

    def _current_ts(self) -> float:
        return self._clock.now().timestamp() if self._clock is not None else time.time()

    def authenticate(self, token: str | None) -> Principal | None:
        if not token or token.count(".") != 1:
            return None
        payload_b64, sig_b64 = token.split(".", 1)
        try:
            payload = _b64url_decode(payload_b64)
            sig = _b64url_decode(sig_b64)
        except (ValueError, TypeError):
            return None
        expected = hmac.new(self._secret, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return None
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict) or float(data.get("exp", 0)) < self._current_ts():
            return None
        scopes = data.get("scopes", [])
        if not isinstance(scopes, list):
            return None
        return Principal(subject=str(data.get("sub", "")), scopes=frozenset(map(str, scopes)))
