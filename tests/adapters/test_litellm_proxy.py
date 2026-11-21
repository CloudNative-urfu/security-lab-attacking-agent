"""Фабрика клиента под LiteLLM Proxy: сборка из env без сети."""

from __future__ import annotations

from typing import Any

from attacking_agent.adapters.litellm_client import LiteLLMClient, proxy_client_from_env


def test_proxy_client_from_env_routes_to_proxy(monkeypatch: Any) -> None:
    monkeypatch.setenv("LITELLM_BASE_URL", "http://litellm:4000/v1")
    monkeypatch.setenv("LITELLM_MODEL", "agent-llm")
    monkeypatch.setenv("LITELLM_VIRTUAL_KEY", "sk-virtual-123")
    client = proxy_client_from_env()
    assert isinstance(client, LiteLLMClient)
    assert client._model == "openai/agent-llm"  # OpenAI-совместимый роутинг
    assert client._extra["api_base"] == "http://litellm:4000/v1"
    assert client._extra["api_key"] == "sk-virtual-123"


def test_proxy_client_falls_back_to_master_key(monkeypatch: Any) -> None:
    monkeypatch.delenv("LITELLM_VIRTUAL_KEY", raising=False)
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-master")
    monkeypatch.setenv("LITELLM_MODEL", "agent-llm")
    client = proxy_client_from_env()
    assert client._extra["api_key"] == "sk-master"


def test_proxy_client_preserves_provider_prefixed_model(monkeypatch: Any) -> None:
    monkeypatch.setenv("LITELLM_MODEL", "openai/custom")
    client = proxy_client_from_env()
    assert client._model == "openai/custom"  # префикс провайдера не дублируется
