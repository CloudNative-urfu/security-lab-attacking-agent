"""Брокерский HTTP-клиент: enforced политика и счётчик на санкционированном пути.

В отличие от кооперативного ``ctx.subrequest()``, этот клиент сам считает каждый
физический запрос и проверяет политику (адрес и цепочку редиректов) на каждом запросе.
Инструменту выдаётся только он — «честный» инструмент не может превысить cap или уйти за
пределы allowlist через санкционированный путь. (Произвольный недоверенный код, открывающий
сокет мимо клиента, ограничивается отдельным сетевым периметром — см. SubprocessToolRunner.)
"""

from __future__ import annotations

import httpx

from attacking_agent.domain.policy import Decision, PolicyEnforcer
from attacking_agent.domain.tools.contracts import SubrequestCapError


class BrokeredRequestDenied(RuntimeError):
    """Запрос инструмента отклонён политикой на уровне брокера."""


class BrokeredHttpClient:
    def __init__(
        self, client: httpx.Client, policy: PolicyEnforcer, max_requests: int
    ) -> None:
        self._client = client
        self._policy = policy
        self._max = max_requests
        self._count = 0

    @property
    def request_count(self) -> int:
        return self._count

    def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        self._count += 1
        if self._count > self._max:
            raise SubrequestCapError(
                f"брокер: превышен cap запросов ({self._max})"
            )
        decision = self._policy.check_address(url)
        if decision.decision is Decision.DENIED:
            raise BrokeredRequestDenied(decision.reason)
        # Автоматические redirects запрещены: перенаправление возвращается как данные и
        # проходит повторную проверку политики инструментом на следующем запросе.
        response = self._client.request(method, url, follow_redirects=False, **kwargs)  # type: ignore[arg-type]
        location = response.headers.get("location")
        if location is not None:
            loc_decision = self._policy.check_address(location)
            if loc_decision.decision is Decision.DENIED:
                raise BrokeredRequestDenied(f"redirect -> {loc_decision.reason}")
        return response
