"""Адаптер CloudRuLLMClient: устойчивый разбор вывода модели, учёт usage/cost, порт LLMClient."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from attacking_agent.adapters.cloudru_client import CloudRuLLMClient
from attacking_agent.domain.budget import (
    BudgetAccountRef,
    InMemoryBudgetStore,
    LedgerManager,
)
from attacking_agent.domain.common.clock import ManualClock
from attacking_agent.domain.common.ids import SequentialIdFactory
from attacking_agent.domain.config import BudgetLevel, BudgetType, Hardness
from attacking_agent.domain.llm import LLMProxy, LLMRequest, ProxyStatus
from attacking_agent.domain.orchestration import StepDecision

DECISION_JSON = (
    '{"kind": "ACTION", "operation": "http.post", "tool": "probe", "params": {"url": "u"}}'
)


class _FakeCompletions:
    def __init__(
        self, content: str, prompt: int = 11, completion: int = 7, reasoning: str = ""
    ) -> None:
        self._content = content
        self._prompt = prompt
        self._completion = completion
        self._reasoning = reasoning
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        message: dict[str, Any] = {"content": self._content}
        if self._reasoning:
            message["reasoning_content"] = self._reasoning
        return {
            "choices": [{"message": message}],
            "usage": {"prompt_tokens": self._prompt, "completion_tokens": self._completion},
        }


class _FakeChatClient:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.chat = type("Chat", (), {"completions": completions})()


def _req(model_ref: str = "model-x") -> LLMRequest:
    return LLMRequest(
        op_id="op-1", attempt_id="att-1", initiator="t", model_ref=model_ref,
        messages=[{"role": "user", "content": "вопрос"}], max_tokens=128,
    )


def _client(content: str, **kw: Any) -> tuple[CloudRuLLMClient, _FakeCompletions]:
    comp = _FakeCompletions(content)
    client = CloudRuLLMClient(chat_factory=lambda: _FakeChatClient(comp), **kw)
    return client, comp


def test_direct_json_parsed_with_usage_and_cost() -> None:
    client, _ = _client(
        DECISION_JSON,
        model="default",
        usd_per_1k_prompt=Decimal("1"),
        usd_per_1k_completion=Decimal("2"),
    )
    resp = client.complete(_req())
    assert resp.structured == {
        "kind": "ACTION", "operation": "http.post", "tool": "probe", "params": {"url": "u"},
    }
    assert resp.usage.prompt_tokens == 11
    assert resp.usage.completion_tokens == 7
    # 11/1000*1 + 7/1000*2 = 0.011 + 0.014
    assert resp.cost == Decimal("0.025")


def test_fenced_json_parsed() -> None:
    client, _ = _client(f"Вот решение:\n```json\n{DECISION_JSON}\n```\n")
    resp = client.complete(_req())
    assert resp.structured is not None
    assert resp.structured["tool"] == "probe"


def test_prose_wrapped_json_parsed() -> None:
    client, _ = _client(f"Думаю так. {DECISION_JSON} Конец.")
    resp = client.complete(_req())
    assert resp.structured is not None
    assert resp.structured["operation"] == "http.post"


def test_non_json_yields_none_structured() -> None:
    client, _ = _client("просто текст без json")
    resp = client.complete(_req())
    assert resp.structured is None
    assert resp.content == "просто текст без json"


def test_reasoning_field_used_when_content_empty() -> None:
    # reasoning-модель: content пуст, но валидный JSON просочился в reasoning_content
    comp = _FakeCompletions("", reasoning=f"Размышляю. {DECISION_JSON}")
    client = CloudRuLLMClient(chat_factory=lambda: _FakeChatClient(comp))
    resp = client.complete(_req())
    assert resp.structured is not None
    assert resp.structured["tool"] == "probe"
    assert resp.content == ""  # сырой content сохраняется как есть


def test_content_json_wins_over_reasoning() -> None:
    comp = _FakeCompletions(DECISION_JSON, reasoning='{"kind": "FINISH"}')
    client = CloudRuLLMClient(chat_factory=lambda: _FakeChatClient(comp))
    resp = client.complete(_req())
    assert resp.structured is not None
    assert resp.structured["kind"] == "ACTION"  # приоритет у content


def test_config_model_used_not_placeholder_ref() -> None:
    client, comp = _client(DECISION_JSON, model="my-deploy")
    client.complete(_req(model_ref="model-x"))
    assert comp.calls[0]["model"] == "my-deploy"  # имя из конфига, не плейсхолдер ref
    assert "response_format" not in comp.calls[0]  # json_mode выключен по умолчанию


def test_json_mode_sets_response_format() -> None:
    client, comp = _client(DECISION_JSON, json_mode=True)
    client.complete(_req())
    assert comp.calls[0]["response_format"] == {"type": "json_object"}


def test_env_sampling_params_flow_into_create(monkeypatch: Any) -> None:
    monkeypatch.setenv("EVOLUTION_TEMPERATURE", "0.2")
    monkeypatch.setenv("EVOLUTION_TOP_P", "0.95")
    monkeypatch.setenv("EVOLUTION_PRESENCE_PENALTY", "0")
    client, comp = _client(DECISION_JSON)
    client.complete(_req())
    assert comp.calls[0]["temperature"] == 0.2
    assert comp.calls[0]["top_p"] == 0.95
    assert comp.calls[0]["presence_penalty"] == 0.0


def test_explicit_extra_overrides_env(monkeypatch: Any) -> None:
    monkeypatch.setenv("EVOLUTION_TEMPERATURE", "0.2")
    client, comp = _client(DECISION_JSON, extra_params={"temperature": 0.9})
    client.complete(_req())
    assert comp.calls[0]["temperature"] == 0.9  # явный параметр важнее env


def test_single_physical_send_per_complete() -> None:
    client, comp = _client(DECISION_JSON)
    client.complete(_req())
    assert len(comp.calls) == 1  # одна физическая отправка, без скрытых retry


def test_validates_through_proxy_as_stepdecision() -> None:
    client, _ = _client(DECISION_JSON)
    mgr = LedgerManager(
        InMemoryBudgetStore(), clock=ManualClock(), id_factory=SequentialIdFactory()
    )
    acc = BudgetAccountRef("c", BudgetLevel.CAMPAIGN, "c", BudgetType.MONEY)
    mgr.open_account(acc, limit=Decimal("1"), hardness=Hardness.HARD, currency="USD")
    proxy = LLMProxy(client=client, ledger=mgr)
    result = proxy.request(
        _req(), account_ref=acc, reserve_amount=Decimal("0.05"),
        reservation_id="res-1", schema=StepDecision,
    )
    assert result.status is ProxyStatus.OK
    assert isinstance(result.structured, StepDecision)
    assert result.structured.kind == "ACTION"
    assert result.structured.tool == "probe"
