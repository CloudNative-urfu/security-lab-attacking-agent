"""Scoped-авторизация со сроком и усиленная редакция секретов."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from attacking_agent.app import create_app
from attacking_agent.app.services import (
    CommandService,
    ReadModelService,
    SignedTokenAuthProvider,
    issue_token,
    strip_secrets,
)

_SECRET = "test-signing-key"


def _client() -> TestClient:
    app = create_app(
        command_service=CommandService(),
        read_models=ReadModelService(state={"camp-1": {"campaign_id": "camp-1"}}),
        auth=SignedTokenAuthProvider(_SECRET),
    )
    return TestClient(app)


def test_expired_token_rejected() -> None:
    client = _client()
    token = issue_token(_SECRET, "op", ["campaign:read"], int(time.time()) - 10)
    r = client.get("/campaigns/camp-1/state", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code in (401, 403)


def test_token_without_scope_rejected() -> None:
    client = _client()
    # есть read, нет write -> команда (write) отклонена
    token = issue_token(_SECRET, "op", ["campaign:read"], int(time.time()) + 3600)
    r = client.post(
        "/campaigns/camp-1/commands",
        json={"command_id": "c1", "kind": "start", "payload": {}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403


def test_valid_scoped_token_ok() -> None:
    client = _client()
    token = issue_token(_SECRET, "op", ["campaign:read"], int(time.time()) + 3600)
    r = client.get("/campaigns/camp-1/state", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_tampered_token_rejected() -> None:
    client = _client()
    token = issue_token(_SECRET, "op", ["campaign:read"], int(time.time()) + 3600)
    tampered = token[:-2] + ("aa" if not token.endswith("aa") else "bb")
    r = client.get("/campaigns/camp-1/state", headers={"Authorization": f"Bearer {tampered}"})
    assert r.status_code in (401, 403)


# --- усиленная редакция секретов -------------------------------------------------


def test_strip_redacts_secret_value_in_neutral_field() -> None:
    data = {"meta": "sk-ABCDEF1234567890abcdef", "note": "обычный текст"}
    out = strip_secrets(data)
    assert out["meta"] == "<secret-redacted>"
    assert out["note"] == "обычный текст"


def test_strip_keeps_benign_field_with_secretish_name() -> None:
    # 'tokenization_method' не содержит слова-маркера целиком -> не вырезается
    data = {"tokenization_method": "bpe", "api_key": "sk-ABCDEF1234567890abcdef"}
    out = strip_secrets(data)
    assert out["tokenization_method"] == "bpe"
    assert "api_key" not in out  # секретное имя поля вырезано


def test_strip_redacts_jwt_anywhere() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJlZGF0YQ"
    out = strip_secrets({"payload": jwt})
    assert out["payload"] == "<secret-redacted>"
