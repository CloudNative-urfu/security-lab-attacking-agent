"""Адаптер :class:`LLMClient` поверх Cloud.ru Evolution ML Inference (`evolution-openai`).

Транспорт OpenAI-совместимый: `client.chat.completions.create(...)`. Библиотека сама
управляет IAM-токеном (key_id/secret → iam.api.cloud.ru, авто-рефреш). Секреты берутся из
окружения и в код не попадают. Скрытые retry/fallback выключены (`max_retries=0`): повтор —
новая учтённая попытка на уровне харнеса, а не скрытый retry внутри клиента.

Дедицированный деплой отдаёт одну модель, поэтому используется сконфигурированное имя
(``model``/``EVOLUTION_MODEL``), а не плейсхолдер ``model_ref`` из доменного слоя.
Структурированный вывод модели извлекается устойчиво (прямой JSON, ```-ограждение, первый
сбалансированный объект), так как строгий ``response_format`` на деплое не гарантирован.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from attacking_agent.domain.llm import LLMRequest, LLMResponse, Usage

ChatFactory = Callable[[], Any]

_FENCE_LANG = re.compile(r"^[a-zA-Z0-9_-]+\s*")


def _build_default_client() -> Any:
    """Собрать `EvolutionOpenAI` из окружения. Секреты не логируются и не хранятся в коде."""
    try:
        from evolution_openai import EvolutionOpenAI
    except ImportError as exc:  # pragma: no cover - зависит от установки опционального пакета
        raise RuntimeError(
            "Не установлен пакет 'evolution-openai'. Установите extra: pip install "
            "'attacking-agent[cloudru]' (или evolution-openai напрямую)."
        ) from exc

    key_id = os.environ.get("EVOLUTION_KEY_ID")
    secret = os.environ.get("EVOLUTION_SECRET")
    base_url = os.environ.get("EVOLUTION_BASE_URL")
    if not (key_id and secret and base_url):
        raise RuntimeError(
            "Не заданы EVOLUTION_KEY_ID / EVOLUTION_SECRET / EVOLUTION_BASE_URL."
        )
    kwargs: dict[str, Any] = {
        "key_id": key_id,
        "secret": secret,
        "base_url": base_url,
        "max_retries": 0,  # никаких скрытых повторов внутри клиента
    }
    token_url = os.environ.get("EVOLUTION_TOKEN_URL")
    if token_url:
        kwargs["token_url"] = token_url
    return EvolutionOpenAI(**kwargs)


def _get(obj: Any, *path: str | int) -> Any:
    cur = obj
    for key in path:
        if cur is None:
            return None
        if isinstance(key, int):
            cur = cur[key] if isinstance(cur, (list, tuple)) and len(cur) > key else None
        elif isinstance(cur, dict):
            cur = cur.get(key)
        else:
            cur = getattr(cur, key, None)
    return cur


def _try_json(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _extract_json(text: str | None) -> dict[str, Any] | None:
    """Достать объект JSON из ответа модели: прямой, в ```-ограждении или в прозе."""
    if not text:
        return None
    direct = _try_json(text)
    if direct is not None:
        return direct

    stripped = text.strip()
    if stripped.startswith("```"):
        parts = stripped.split("```")
        if len(parts) >= 2:
            body = _FENCE_LANG.sub("", parts[1].strip(), count=1)
            fenced = _try_json(body.strip())
            if fenced is not None:
                return fenced

    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return _try_json(text[start : i + 1])
    return None


class CloudRuLLMClient:
    """Порт транспорта модели поверх Cloud.ru Evolution ML Inference.

    ``chat_factory`` инъектируется для юнит-тестов (возвращает объект с
    ``.chat.completions.create(...)``), по умолчанию лениво строит `EvolutionOpenAI` из env.
    Цена за токены опциональна: при нулевой цене денежный счёт не ограничивает, но счёт
    запросов (REQUESTS) всё равно ограничивает число вызовов.
    """

    def __init__(
        self,
        model: str | None = None,
        chat_factory: ChatFactory | None = None,
        usd_per_1k_prompt: Decimal = Decimal("0"),
        usd_per_1k_completion: Decimal = Decimal("0"),
        json_mode: bool | None = None,
        extra_params: dict[str, Any] | None = None,
    ) -> None:
        self._model = model or os.environ.get("EVOLUTION_MODEL", "default")
        self._factory = chat_factory or _build_default_client
        self._client: Any = None
        self._price_prompt = usd_per_1k_prompt
        self._price_completion = usd_per_1k_completion
        if json_mode is None:
            json_mode = os.environ.get("EVOLUTION_JSON_MODE", "").lower() in ("1", "true", "yes")
        self._json_mode = json_mode
        self._extra = extra_params or {}

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = self._factory()
        return self._client

    def _cost(self, prompt_tokens: int, completion_tokens: int) -> Decimal:
        return (
            Decimal(prompt_tokens) / 1000 * self._price_prompt
            + Decimal(completion_tokens) / 1000 * self._price_completion
        )

    def complete(self, request: LLMRequest) -> LLMResponse:
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": self._model,  # дедицированный деплой: одно имя модели
            "messages": request.messages,
            "max_tokens": request.max_tokens,
            **self._extra,
        }
        if self._json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = client.chat.completions.create(**kwargs)

        content = _get(response, "choices", 0, "message", "content")
        prompt_tokens = int(_get(response, "usage", "prompt_tokens") or 0)
        completion_tokens = int(_get(response, "usage", "completion_tokens") or 0)
        return LLMResponse(
            content=str(content or ""),
            structured=_extract_json(content if isinstance(content, str) else None),
            usage=Usage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
            cost=self._cost(prompt_tokens, completion_tokens),
        )
