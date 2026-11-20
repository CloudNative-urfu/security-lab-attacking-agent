"""Контракт capability operator-api."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from attacking_agent.app import create_app
from attacking_agent.app.services import (
    CommandService,
    ReadModelService,
    StaticAuthProvider,
)


@pytest.fixture
def client() -> TestClient:
    projection = {
        "campaign_id": "camp-1",
        "objective": {"zone": "форма обратной связи"},
        "interfaces": [{"name": "public-http", "address": "https://demo.svc.internal/feedback"}],
        "allowed_operations": ["http.post"],
    }
    app = create_app(
        command_service=CommandService(),
        read_models=ReadModelService(state={"camp-1": projection}),
        auth=StaticAuthProvider(valid_tokens={"valid-token"}),
    )
    return TestClient(app)


def _cmd(command_id: str = "cmd-1", kind: str = "start", target: str = "br-1") -> dict:
    return {"command_id": command_id, "kind": kind, "payload": {"branch_id": target}}


def test_command_idempotent_same_content(client: TestClient) -> None:
    r1 = client.post("/campaigns/camp-1/commands", json=_cmd())
    r2 = client.post("/campaigns/camp-1/commands", json=_cmd())
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["workflow_ref"] == r2.json()["workflow_ref"]  # прежний результат
    assert r2.json()["status"] == "duplicate"


def test_command_conflict_same_id_diff_content(client: TestClient) -> None:
    client.post("/campaigns/camp-1/commands", json=_cmd("cmd-1", "start", "br-1"))
    r = client.post("/campaigns/camp-1/commands", json=_cmd("cmd-1", "start", "br-2"))
    assert r.status_code == 409


def test_read_requires_auth(client: TestClient) -> None:
    r = client.get("/campaigns/camp-1/state")
    assert r.status_code in (401, 403)


def test_read_with_auth_ok(client: TestClient) -> None:
    r = client.get("/campaigns/camp-1/state", headers={"Authorization": "Bearer valid-token"})
    assert r.status_code == 200
    assert r.json()["campaign_id"] == "camp-1"


def test_export_excludes_secrets(client: TestClient) -> None:
    r = client.get(
        "/campaigns/camp-1/export", headers={"Authorization": "Bearer valid-token"}
    )
    assert r.status_code == 200
    text = r.text
    assert "vault://" not in text
    assert "auth_ref" not in text
    assert "admin" not in text.lower()


def test_invalid_token_rejected(client: TestClient) -> None:
    r = client.get("/campaigns/camp-1/state", headers={"Authorization": "Bearer nope"})
    assert r.status_code in (401, 403)
