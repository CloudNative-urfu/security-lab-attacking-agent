"""Контракт capability policy-enforcement."""

from __future__ import annotations

from attacking_agent.domain.config import PolicySpec
from attacking_agent.domain.policy import (
    Decision,
    PolicyEnforcer,
    StaticAddressResolver,
)


def _spec() -> PolicySpec:
    return PolicySpec(
        policy_version="p1",
        allowed_operations=["http.get", "http.post"],
        allowed_addresses=["https://demo.svc.internal/*"],
    )


def _enforcer(resolver: StaticAddressResolver | None = None) -> PolicyEnforcer:
    resolver = resolver or StaticAddressResolver({"demo.svc.internal": ["203.0.113.10"]})
    return PolicyEnforcer(_spec(), resolver=resolver)


def test_operation_in_allowlist_allowed() -> None:
    res = _enforcer().check_operation("http.get")
    assert res.decision is Decision.ALLOWED
    assert res.policy_version == "p1"


def test_operation_not_in_allowlist_denied() -> None:
    res = _enforcer().check_operation("fs.read")
    assert res.decision is Decision.DENIED
    assert res.policy_version == "p1"


def test_llm_proposed_operation_still_denied() -> None:
    # Факт предложения моделью не расширяет allowlist.
    res = _enforcer().check_operation("http.delete")
    assert res.decision is Decision.DENIED


def test_allowed_address_allowed() -> None:
    res = _enforcer().check_address("https://demo.svc.internal/feedback")
    assert res.decision is Decision.ALLOWED


def test_redirect_to_address_outside_allowlist_denied() -> None:
    # Исходный разрешён, но цепочка редиректов ведёт наружу.
    chain = ["https://demo.svc.internal/feedback", "https://evil.example/collect"]
    res = _enforcer().check_request_chain(chain)
    assert res.decision is Decision.DENIED


def test_resolution_to_metadata_address_denied() -> None:
    resolver = StaticAddressResolver({"demo.svc.internal": ["169.254.169.254"]})
    res = _enforcer(resolver).check_address("https://demo.svc.internal/feedback")
    assert res.decision is Decision.DENIED
    assert "резолв" in res.reason.lower() or "169.254" in res.reason


def test_resolution_to_loopback_denied() -> None:
    resolver = StaticAddressResolver({"demo.svc.internal": ["127.0.0.1"]})
    res = _enforcer(resolver).check_address("https://demo.svc.internal/feedback")
    assert res.decision is Decision.DENIED


def test_string_allowed_but_chain_all_hops_checked() -> None:
    # Все хопы разрешены — цепочка проходит.
    chain = [
        "https://demo.svc.internal/a",
        "https://demo.svc.internal/b",
    ]
    res = _enforcer().check_request_chain(chain)
    assert res.decision is Decision.ALLOWED
