"""Адаптер :class:`LLMClient` поверх LiteLLM (ChatLiteLLM-совместимый транспорт).

Создаётся на worker-слое; provider/router retries и fallback явно выключены — повтор
является новой учтённой попыткой на уровне харнеса, а не скрытым retry внутри клиента.
``completion_fn`` инъектируется, чтобы адаптер можно было юнит-тестировать без сети.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from attacking_agent.domain.llm import LLMRequest, LLMResponse, Usage


def _default_completion(**kwargs: Any) -> Any:
    import litellm

    return litellm.completion(**kwargs)


def _default_cost(response: Any) -> Decimal:
    try:
        import litellm

        return Decimal(str(litellm.completion_cost(completion_response=response)))
    except Exception:
        return Decimal("0")


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


class LiteLLMClient:
    def __init__(
        self,
        model: str,
        completion_fn: Callable[..., Any] | None = None,
        cost_fn: Callable[[Any], Decimal] | None = None,
        extra_params: dict[str, Any] | None = None,
    ) -> None:
        self._model = model
        self._completion = completion_fn or _default_completion
        self._cost = cost_fn or _default_cost
        self._extra = extra_params or {}

    def complete(self, request: LLMRequest) -> LLMResponse:
        response = self._completion(
            model=request.model_ref or self._model,
            messages=request.messages,
            max_tokens=request.max_tokens,
            num_retries=0,  # никаких скрытых повторов
            fallbacks=[],  # никакого скрытого fallback
            **self._extra,
        )
        content = _get(response, "choices", 0, "message", "content")
        if content is None:
            choices = _get(response, "choices")
            if isinstance(choices, list) and choices:
                content = _get(choices[0], "message", "content")
        prompt_tokens = int(_get(response, "usage", "prompt_tokens") or 0)
        completion_tokens = int(_get(response, "usage", "completion_tokens") or 0)
        return LLMResponse(
            content=str(content or ""),
            structured=_try_json(content),
            usage=Usage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
            cost=self._cost(response),
        )
