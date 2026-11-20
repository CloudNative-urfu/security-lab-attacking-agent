"""Юнит-тесты инфраструктурных адаптеров (httpx-инструмент, LiteLLM) без сети."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx

from attacking_agent.adapters.http_tool import HttpParams, make_http_tool
from attacking_agent.adapters.litellm_client import LiteLLMClient
from attacking_agent.domain.llm import LLMRequest
from attacking_agent.domain.tools import LocalToolRunner, RunRequest, ToolContext


def test_http_tool_counts_subrequest_and_no_autoredirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/r":
            return httpx.Response(302, headers={"location": "https://demo.svc.internal/next"})
        return httpx.Response(200, text="ok")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    contract = make_http_tool("http_get", "http.get", client)
    runner = LocalToolRunner(subrequest_cap=5)

    # обычный ответ
    obs = runner.run(
        RunRequest(
            executor=contract.executor,
            params=HttpParams(url="https://demo.svc.internal/ok", method="GET"),
            address="https://demo.svc.internal/ok",
        )
    )
    assert obs.status.value == "ok"
    assert obs.subrequests == 1  # учтён один физический подзапрос
    assert obs.payload["status_code"] == 200

    # redirect не выполняется автоматически — возвращается как наблюдение
    obs2 = runner.run(
        RunRequest(
            executor=contract.executor,
            params=HttpParams(url="https://demo.svc.internal/r", method="GET"),
            address="https://demo.svc.internal/r",
        )
    )
    assert obs2.payload["status_code"] == 302
    assert obs2.payload["redirect_location"] == "https://demo.svc.internal/next"


def test_http_tool_context_is_isolated() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    contract = make_http_tool("http_get", "http.get", client)
    captured: dict[str, Any] = {}

    orig = contract.executor

    def wrap(ctx: ToolContext, params: HttpParams) -> dict[str, Any]:
        captured["ctx"] = ctx
        return orig(ctx, params)

    LocalToolRunner().run(
        RunRequest(executor=wrap, params=HttpParams(url="https://x/"), address="https://x/")
    )
    for forbidden in ("secrets", "db", "model_key", "credentials"):
        assert not hasattr(captured["ctx"], forbidden)


def test_litellm_client_maps_response_and_disables_retries() -> None:
    seen: dict[str, Any] = {}

    def fake_completion(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {
            "choices": [{"message": {"content": '{"kind": "FINISH", "outcome": "SUPPORTED"}'}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 7},
        }

    client = LiteLLMClient(
        model="model-x",
        completion_fn=fake_completion,
        cost_fn=lambda _resp: Decimal("0.0005"),
    )
    req = LLMRequest(
        op_id="op-1",
        attempt_id="att-1",
        initiator="test",
        model_ref="model-x",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=64,
    )
    resp = client.complete(req)
    assert resp.structured == {"kind": "FINISH", "outcome": "SUPPORTED"}
    assert resp.usage.prompt_tokens == 12
    assert resp.cost == Decimal("0.0005")
    # скрытые повторы и fallback выключены
    assert seen["num_retries"] == 0
    assert seen["fallbacks"] == []
