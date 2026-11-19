"""Модели и агрегат КИК (конфигурации исследовательской кампании).

КИК неизменяема после freeze и адресуется ``config_version`` и ``config_hash``
(SHA-256 над канонизированным составом). Секреты допускаются только как ссылки
(ref), а не как значения; это проверяется до типизированной валидации.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from enum import Enum, StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from attacking_agent.domain.common.errors import (
    ImmutabilityError,
    SecretLeakError,
    ValidationError,
)

# Имена ключей, значение которых обязано быть ссылкой (ref), а не сырым секретом.
_SECRET_KEY_MARKERS = (
    "password",
    "passwd",
    "secret",
    "token",
    "apikey",
    "api_key",
    "api-key",
    "credential",
    "private_key",
    "privatekey",
    "access_key",
    "client_secret",
)


def _is_ref_shaped(value: Any) -> bool:
    return isinstance(value, dict) and set(value.keys()) == {"ref"} and bool(value["ref"])


def _key_is_secret(key: str) -> bool:
    low = key.lower()
    return any(marker in low for marker in _SECRET_KEY_MARKERS)


def scan_for_inline_secrets(data: Any, path: str = "") -> None:
    """Рекурсивно обойти сырой состав и запретить сырые секреты.

    Ключ с «секретным» именем допускается только если его значение имеет форму
    ``{"ref": ...}``. Иначе поднимается :class:`SecretLeakError`.
    """

    if isinstance(data, dict):
        for key, value in data.items():
            here = f"{path}.{key}" if path else str(key)
            if _key_is_secret(str(key)) and not _is_ref_shaped(value):
                raise SecretLeakError(
                    f"поле '{here}' выглядит как секрет, но не является ссылкой (ref)"
                )
            scan_for_inline_secrets(value, here)
    elif isinstance(data, list):
        for i, item in enumerate(data):
            scan_for_inline_secrets(item, f"{path}[{i}]")


class SecretRef(BaseModel):
    """Ссылка на секрет; сам секрет в КИК не хранится."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    ref: str = Field(min_length=1)


class InterfaceKind(StrEnum):
    HTTP = "http"
    WS = "ws"
    FILE = "file"


class BudgetType(StrEnum):
    REQUESTS = "requests"
    TOKENS = "tokens"
    TIME = "time"
    MONEY = "money"
    EPISODES = "episodes"


class BudgetLevel(StrEnum):
    CAMPAIGN = "campaign"
    BRANCH = "branch"
    EPISODE = "episode"


class Hardness(StrEnum):
    HARD = "hard"
    SOFT = "soft"


class ObjectiveSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    zone: str = Field(min_length=1)
    observation_criteria: list[str] = Field(min_length=1)
    uncertainty: str = ""


class TargetSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    target_ref: str = Field(min_length=1)
    version: str = Field(min_length=1)
    env_binding: str = Field(min_length=1)


class InterfaceSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    name: str = Field(min_length=1)
    kind: InterfaceKind
    address: str = Field(min_length=1)
    capability_refs: list[str] = Field(default_factory=list)
    auth_ref: SecretRef | None = None


class BudgetSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    budget_type: BudgetType
    level: BudgetLevel
    hardness: Hardness
    limit: Decimal
    currency: str | None = None

    def model_post_init(self, _ctx: Any) -> None:
        if self.budget_type is BudgetType.MONEY and not self.currency:
            raise ValueError("денежный бюджет требует currency")
        if self.limit < Decimal("0"):
            raise ValueError("лимит бюджета не может быть отрицательным")


class PolicySpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    policy_version: str = Field(min_length=1)
    allowed_operations: list[str] = Field(default_factory=list)
    allowed_addresses: list[str] = Field(default_factory=list)


class MemoryStateSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    start_checkpoint_ref: str | None = None
    full_restore_required: bool = True


class _PublicInterface(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    kind: InterfaceKind
    address: str
    capability_refs: list[str]


class PublicProjection(BaseModel):
    """Публичная проекция КИК для атакующего: без секретов и приватных данных."""

    model_config = ConfigDict(frozen=True)
    objective: ObjectiveSpec
    target_ref: str
    target_version: str
    interfaces: list[_PublicInterface]
    allowed_operations: list[str]
    allowed_addresses: list[str]


class CampaignConfig(BaseModel):
    """Редактируемый агрегат КИК до freeze."""

    model_config = ConfigDict(extra="ignore")

    objective: ObjectiveSpec
    target: TargetSpec
    interfaces: list[InterfaceSpec] = Field(min_length=1)
    budgets: list[BudgetSpec] = Field(min_length=1)
    policy: PolicySpec
    memory_state: MemoryStateSpec
    private_refs: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_raw(cls, data: dict[str, Any]) -> CampaignConfig:
        """Построить КИК из сырого состава (как из YAML/JSON) с проверкой секретов."""
        scan_for_inline_secrets(data)
        try:
            return cls.model_validate(data)
        except PydanticValidationError as exc:
            raise ValidationError(str(exc)) from exc

    def _canonical(self) -> str:
        payload = self.model_dump(mode="python", exclude={"private_refs"})
        payload["private_refs"] = self.private_refs
        normalized = _normalize(payload)
        return json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    def freeze(self, config_version: str | None = None) -> FrozenCampaignConfig:
        canonical = self._canonical()
        config_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        version = config_version or f"cfg-{config_hash[:12]}"
        return FrozenCampaignConfig(
            objective=self.objective,
            target=self.target,
            interfaces=self.interfaces,
            budgets=self.budgets,
            policy=self.policy,
            memory_state=self.memory_state,
            private_refs=self.private_refs,
            config_hash=config_hash,
            config_version=version,
        )


class FrozenCampaignConfig(CampaignConfig):
    """Неизменяемая замороженная КИК с версией и хэшем."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    config_hash: str
    config_version: str

    def set_field(self, name: str, value: Any) -> None:
        """Явная попытка изменения — всегда запрещена."""
        raise ImmutabilityError(
            f"КИК заморожена; поле '{name}' нельзя изменить (попытка значения {value!r})"
        )

    def public_projection(self) -> PublicProjection:
        return PublicProjection(
            objective=self.objective,
            target_ref=self.target.target_ref,
            target_version=self.target.version,
            interfaces=[
                _PublicInterface(
                    name=i.name,
                    kind=i.kind,
                    address=i.address,
                    capability_refs=list(i.capability_refs),
                )
                for i in self.interfaces
            ],
            allowed_operations=list(self.policy.allowed_operations),
            allowed_addresses=list(self.policy.allowed_addresses),
        )


def _normalize(value: Any) -> Any:
    """Рекурсивно привести состав к детерминированно сериализуемому виду."""
    if isinstance(value, Decimal):
        return f"D:{value.normalize():f}"
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    return value
