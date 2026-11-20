"""Entrypoint LLM-шлюза. Запуск: python -m services.llm_gateway

Транспорт к модели через LiteLLM (модель — из AG_MODEL). Ключи/настройки провайдера —
из окружения LiteLLM; скрытые retry/fallback выключены на уровне адаптера.
"""

from __future__ import annotations

import os

import uvicorn

from attacking_agent.adapters.litellm_client import LiteLLMClient
from services.llm_gateway.app import create_gateway_app

app = create_gateway_app(LiteLLMClient(model=os.environ.get("AG_MODEL", "gpt-4o-mini")))


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("AG_PORT", "8081")))  # noqa: S104


if __name__ == "__main__":
    main()
