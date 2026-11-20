"""Контракт capability campaign-config (КИК)."""

from __future__ import annotations

import copy

import pytest

from attacking_agent.domain.common.errors import (
    ImmutabilityError,
    SecretLeakError,
    ValidationError,
)
from attacking_agent.domain.config import CampaignConfig


def _valid_raw() -> dict:
    return {
        "objective": {
            "zone": "Форма обратной связи публичного портала",
            "observation_criteria": ["ответ 200 с отражённым вводом"],
            "uncertainty": "неизвестно наличие серверной валидации",
        },
        "target": {
            "target_ref": "svc-demo",
            "version": "1.4.2",
            "env_binding": "staging-instance-7",
        },
        "interfaces": [
            {
                "name": "public-http",
                "kind": "http",
                "address": "https://demo.svc.internal/feedback",
                "capability_refs": ["submit-feedback"],
                "auth_ref": {"ref": "vault://demo/feedback-token"},
            }
        ],
        "budgets": [
            {"budget_type": "requests", "level": "campaign", "hardness": "hard", "limit": 100},
            {
                "budget_type": "money",
                "level": "campaign",
                "hardness": "hard",
                "limit": "5.00",
                "currency": "USD",
            },
        ],
        "policy": {
            "policy_version": "p1",
            "allowed_operations": ["http.get", "http.post"],
            "allowed_addresses": ["https://demo.svc.internal/*"],
        },
        "memory_state": {
            "start_checkpoint_ref": None,
            "full_restore_required": True,
        },
        "private_refs": {
            "admin_credentials": {"ref": "vault://demo/admin"},
            "hidden_measurements": ["internal-detector-score"],
        },
    }


def test_valid_config_accepted() -> None:
    cfg = CampaignConfig.from_raw(_valid_raw())
    assert cfg.objective.zone.startswith("Форма")
    assert len(cfg.interfaces) == 1


def test_missing_required_section_rejected() -> None:
    raw = _valid_raw()
    del raw["budgets"]
    with pytest.raises(ValidationError):
        CampaignConfig.from_raw(raw)


def test_inline_secret_rejected() -> None:
    raw = _valid_raw()
    raw["interfaces"][0]["auth_ref"] = {"password": "hunter2"}
    with pytest.raises(SecretLeakError):
        CampaignConfig.from_raw(raw)


def test_inline_secret_by_key_name_rejected() -> None:
    raw = _valid_raw()
    raw["target"]["api_key"] = "sk-raw-inline-value"
    with pytest.raises(SecretLeakError):
        CampaignConfig.from_raw(raw)


def test_ref_shaped_secret_accepted() -> None:
    raw = _valid_raw()
    raw["target"]["api_key"] = {"ref": "vault://demo/key"}
    cfg = CampaignConfig.from_raw(raw)
    assert cfg is not None


def test_hash_is_deterministic_under_key_order() -> None:
    raw1 = _valid_raw()
    raw2 = copy.deepcopy(raw1)
    # перемешиваем порядок ключей верхнего уровня
    raw2 = dict(reversed(list(raw2.items())))
    frozen1 = CampaignConfig.from_raw(raw1).freeze()
    frozen2 = CampaignConfig.from_raw(raw2).freeze()
    assert frozen1.config_hash == frozen2.config_hash
    assert len(frozen1.config_hash) == 64  # sha-256 hex


def test_changing_content_changes_hash() -> None:
    raw1 = _valid_raw()
    raw2 = copy.deepcopy(raw1)
    raw2["budgets"][0]["limit"] = 101
    h1 = CampaignConfig.from_raw(raw1).freeze().config_hash
    h2 = CampaignConfig.from_raw(raw2).freeze().config_hash
    assert h1 != h2


def test_frozen_config_is_immutable() -> None:
    frozen = CampaignConfig.from_raw(_valid_raw()).freeze()
    with pytest.raises(ImmutabilityError):
        frozen.set_field("config_version", "tampered")


def test_public_projection_excludes_private_and_secrets() -> None:
    frozen = CampaignConfig.from_raw(_valid_raw()).freeze()
    projection = frozen.public_projection()
    dumped = projection.model_dump()
    text = str(dumped)
    # нет приватных секций и секретов
    assert "admin_credentials" not in text
    assert "hidden_measurements" not in text
    assert "auth_ref" not in text
    assert "vault://" not in text
    # но разрешённые интерфейсы и цель присутствуют
    assert dumped["interfaces"][0]["address"] == "https://demo.svc.internal/feedback"
    assert dumped["objective"]["zone"].startswith("Форма")


def test_secret_value_in_neutral_field_rejected() -> None:
    raw = _valid_raw()
    raw["objective"]["note"] = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl"  # JWT-подобное
    with pytest.raises(SecretLeakError):
        CampaignConfig.from_raw(raw)
