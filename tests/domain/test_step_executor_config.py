"""Конфигурация шага: max_tokens прокидывается в запрос к модели (нужно reasoning-моделям)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, ClassVar

from attacking_agent.domain.budget import BudgetAccountRef
from attacking_agent.domain.common.scope import Scope
from attacking_agent.domain.config import BudgetLevel, BudgetType
from attacking_agent.domain.llm import ProxyResult, ProxyStatus
from attacking_agent.domain.orchestration import EpisodeSpec, MetricsControl, StepDecision
from attacking_agent.domain.orchestration.step_executor import EpisodeStepExecutor


class _FakeRead:
    knowledge: ClassVar[list[Any]] = []


class _FakeReader:
    def read(self, *args: Any, **kwargs: Any) -> _FakeRead:
        return _FakeRead()


class _CapturingProxy:
    def __init__(self) -> None:
        self.max_tokens: int | None = None

    def request(self, req: Any, **kwargs: Any) -> ProxyResult:
        self.max_tokens = req.max_tokens
        return ProxyResult(
            ProxyStatus.OK, response=None,
            structured=StepDecision(kind="FINISH", outcome="SUPPORTED"),
        )


def _acc(bt: BudgetType) -> BudgetAccountRef:
    return BudgetAccountRef("c", BudgetLevel.CAMPAIGN, "c", bt)


def _executor(proxy: _CapturingProxy, **kw: Any) -> EpisodeStepExecutor:
    return EpisodeStepExecutor(
        reader=_FakeReader(), writer=None, proxy=proxy, dispatcher=None,  # type: ignore[arg-type]
        policy=None, metrics=MetricsControl(),  # type: ignore[arg-type]
        llm_account=_acc(BudgetType.MONEY), tool_account=_acc(BudgetType.REQUESTS),
        llm_reserve=Decimal("0"), tool_reserve=Decimal("0"), **kw,
    )


_SPEC = EpisodeSpec("ep", Scope(campaign_id="c", branch_id="b", target_ref="t"), "q", max_steps=1)


def test_executor_uses_configured_max_tokens() -> None:
    proxy = _CapturingProxy()
    pr = _executor(proxy, max_tokens=777).propose(_SPEC, 0, [])
    assert proxy.max_tokens == 777
    assert pr.ok and pr.kind == "FINISH"


def test_default_max_tokens_fits_reasoning() -> None:
    proxy = _CapturingProxy()
    _executor(proxy).propose(_SPEC, 0, [])
    assert proxy.max_tokens is not None and proxy.max_tokens >= 1024  # не 256
