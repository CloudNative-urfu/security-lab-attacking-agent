"""Проверка ограничений кампании (deny-by-default) и областей.

Решение всегда ссылается на ``policy_version``. Адрес проверяется и как строка
(шаблон allowlist для каждого хопа редиректа), и после разрешения имени — против
набора запрещённых диапазонов (link-local/metadata, loopback), чтобы строковой
проверки URL было недостаточно для обхода.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatchcase
from typing import Protocol
from urllib.parse import urlsplit

from attacking_agent.domain.config import PolicySpec

# Диапазоны, запрещённые по умолчанию независимо от allowlist (защита от SSRF).
_DEFAULT_DENIED_NETWORKS: tuple[str, ...] = (
    "127.0.0.0/8",  # loopback
    "::1/128",
    "169.254.0.0/16",  # link-local, включая cloud metadata 169.254.169.254
    "fe80::/10",
)


class Decision(StrEnum):
    ALLOWED = "allowed"
    DENIED = "denied"


@dataclass(frozen=True, slots=True)
class PolicyResult:
    decision: Decision
    policy_version: str
    reason: str = ""


class AddressResolver(Protocol):
    """Порт разрешения имени в список IP-адресов."""

    def resolve(self, host: str) -> list[str]: ...


class StaticAddressResolver:
    """Детерминированный резолвер для тестов и статических привязок."""

    def __init__(self, mapping: dict[str, list[str]]) -> None:
        self._mapping = mapping

    def resolve(self, host: str) -> list[str]:
        return list(self._mapping.get(host, []))


class SystemAddressResolver:
    """Резолвер через системный DNS (production)."""

    def resolve(self, host: str) -> list[str]:
        import socket

        infos = socket.getaddrinfo(host, None)
        return [str(info[4][0]) for info in infos]


class PolicyEnforcer:
    """Доменная проверка ограничений поверх :class:`PolicySpec`."""

    def __init__(
        self,
        spec: PolicySpec,
        resolver: AddressResolver,
        denied_networks: tuple[str, ...] = _DEFAULT_DENIED_NETWORKS,
    ) -> None:
        self._spec = spec
        self._resolver = resolver
        self._denied = [ipaddress.ip_network(n) for n in denied_networks]

    @property
    def policy_version(self) -> str:
        return self._spec.policy_version

    def _allow(self, reason: str = "") -> PolicyResult:
        return PolicyResult(Decision.ALLOWED, self.policy_version, reason)

    def _deny(self, reason: str) -> PolicyResult:
        return PolicyResult(Decision.DENIED, self.policy_version, reason)

    def check_operation(self, operation: str) -> PolicyResult:
        if operation in self._spec.allowed_operations:
            return self._allow()
        return self._deny(f"операция '{operation}' вне allowlist")

    def _matches_allowlist(self, url: str) -> bool:
        return any(fnmatchcase(url, pattern) for pattern in self._spec.allowed_addresses)

    def _resolved_address_denied(self, host: str) -> str | None:
        for ip_str in self._resolver.resolve(host):
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                return f"нераспознанный адрес резолвинга: {ip_str}"
            for net in self._denied:
                if ip.version == net.version and ip in net:
                    return f"резолвинг '{host}' в запрещённый адрес {ip_str}"
        return None

    def check_address(self, url: str) -> PolicyResult:
        if not self._matches_allowlist(url):
            return self._deny(f"адрес '{url}' вне allowlist")
        host = urlsplit(url).hostname or ""
        denied_reason = self._resolved_address_denied(host)
        if denied_reason is not None:
            return self._deny(denied_reason)
        return self._allow()

    def check_request_chain(self, urls: list[str]) -> PolicyResult:
        """Проверить каждый хоп цепочки (исходный URL и все редиректы)."""
        for url in urls:
            res = self.check_address(url)
            if res.decision is Decision.DENIED:
                return res
        return self._allow()
