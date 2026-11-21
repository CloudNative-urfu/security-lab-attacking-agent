#!/usr/bin/env python3
"""Живая проверка LLM Cloud.ru: один реальный запрос через полный путь агента.

Читает конфигурацию и секреты ТОЛЬКО из окружения (или из локального .env, если он есть).
Секреты в код/вывод не попадают. Проверяет, что живая модель отдаёт валидный StepDecision.

Запуск (после заполнения .env):
    uv run python scripts/smoke_cloudru.py
    # или: set -a; source .env; set +a; python scripts/smoke_cloudru.py
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Минимальный загрузчик .env (без зависимостей); не переопределяет уже заданные переменные."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def main() -> int:
    _load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    missing = [
        name
        for name in ("EVOLUTION_KEY_ID", "EVOLUTION_SECRET", "EVOLUTION_BASE_URL")
        if not os.environ.get(name)
    ]
    if missing:
        names = ", ".join(missing)
        print(f"[smoke] не заданы переменные: {names} — заполните .env", file=sys.stderr)
        return 2

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
    from attacking_agent.domain.orchestration.prompt import build_messages

    client = CloudRuLLMClient()
    messages = build_messages(
        question="Проверка связи. Верни kind=INCONCLUSIVE одним объектом JSON.",
        evidence=[],
        observations=[],
    )

    # --- Сырой диагностический вызов: увидеть, что реально отдаёт деплой ---
    raw = client._get_client().chat.completions.create(
        model=os.environ.get("EVOLUTION_MODEL", "default"),
        messages=messages,
        max_tokens=2048,
    )
    choice = raw.choices[0]
    msg = choice.message
    print(f"[smoke] finish_reason: {choice.finish_reason}")
    print(f"[smoke] content: {(msg.content or '')[:400]!r}")
    for field in ("reasoning_content", "reasoning"):
        val = getattr(msg, field, None)
        if val:
            print(f"[smoke] {field} (до 400): {str(val)[:400]!r}")

    mgr = LedgerManager(
        InMemoryBudgetStore(), clock=ManualClock(), id_factory=SequentialIdFactory()
    )
    acc = BudgetAccountRef("smoke", BudgetLevel.CAMPAIGN, "smoke", BudgetType.MONEY)
    mgr.open_account(acc, limit=Decimal("5"), hardness=Hardness.HARD, currency="USD")
    proxy = LLMProxy(client=client, ledger=mgr)

    req = LLMRequest(
        op_id="smoke-op",
        attempt_id="smoke-att",
        initiator="smoke",
        model_ref="model-x",
        messages=messages,
        max_tokens=2048,
    )
    result = proxy.request(
        req, account_ref=acc, reserve_amount=Decimal("1"),
        reservation_id="smoke-res", schema=StepDecision,
    )

    print(f"[smoke] модель: {os.environ.get('EVOLUTION_MODEL', 'default')}")
    print(f"[smoke] статус: {result.status.value}")
    if result.response is not None:
        usage = result.response.usage.total_tokens
        print(f"[smoke] usage: {usage} ток., cost={result.response.cost}")
        print(f"[smoke] сырой ответ (до 300): {result.response.content[:300]!r}")
    if result.status is ProxyStatus.OK and isinstance(result.structured, StepDecision):
        print(f"[smoke] OK — валидный StepDecision: kind={result.structured.kind}")
        return 0
    print(f"[smoke] НЕ прошло: {result.reason}", file=sys.stderr)
    if result.status is ProxyStatus.INVALID_OUTPUT:
        print(
            "[smoke] подсказка: включите EVOLUTION_JSON_MODE=1 или усильте контракт в prompt.py",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
