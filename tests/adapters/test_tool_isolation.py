"""Состязательные тесты субпроцессной изоляции инструментов."""

from __future__ import annotations

import os

from attacking_agent.adapters.sample_tools import (
    EchoParams,
    EnvProbeParams,
    SizeParams,
    SleepParams,
    big_output_tool,
    echo_tool,
    env_probe_tool,
    sleep_tool,
)
from attacking_agent.adapters.subprocess_runner import SubprocessToolRunner
from attacking_agent.domain.tools import ObservationStatus, RunRequest


def test_subprocess_isolates_parent_secret_env() -> None:
    os.environ["AG_TEST_SECRET"] = "super-secret-value"
    try:
        runner = SubprocessToolRunner(timeout_seconds=15)
        obs = runner.run(
            RunRequest(executor=env_probe_tool, params=EnvProbeParams(var="AG_TEST_SECRET"),
                       address=None)
        )
        assert obs.status is ObservationStatus.OK
        assert obs.payload["saw_secret"] is False  # секрет родителя вычищен в дочернем процессе
    finally:
        del os.environ["AG_TEST_SECRET"]


def test_subprocess_timeout_is_managed() -> None:
    runner = SubprocessToolRunner(timeout_seconds=0.3)
    obs = runner.run(
        RunRequest(executor=sleep_tool, params=SleepParams(seconds=5.0), address=None)
    )
    assert obs.status is ObservationStatus.UNKNOWN
    assert "таймаут" in (obs.error or "")


def test_subprocess_output_cap() -> None:
    runner = SubprocessToolRunner(timeout_seconds=15, max_output_bytes=1024)
    obs = runner.run(
        RunRequest(executor=big_output_tool, params=SizeParams(size=10000), address=None)
    )
    assert obs.status is ObservationStatus.ERROR
    assert "cap" in (obs.error or "").lower()


def test_subprocess_happy_path() -> None:
    runner = SubprocessToolRunner(timeout_seconds=15)
    obs = runner.run(
        RunRequest(executor=echo_tool, params=EchoParams(body="probe"), address=None)
    )
    assert obs.status is ObservationStatus.OK
    assert obs.payload["reflected"] == "probe"
    assert obs.subrequests == 1


def test_non_module_executor_rejected() -> None:
    runner = SubprocessToolRunner()
    obs = runner.run(
        RunRequest(executor=lambda ctx, p: {}, params=EchoParams(), address=None)
    )
    assert obs.status is ObservationStatus.ERROR


# --- Брокерский HTTP-клиент: enforced политика и cap ----------------------------


def _policy():
    from attacking_agent.domain.config import PolicySpec
    from attacking_agent.domain.policy import PolicyEnforcer, StaticAddressResolver

    return PolicyEnforcer(
        PolicySpec(
            policy_version="p1",
            allowed_operations=["http.get"],
            allowed_addresses=["https://demo.svc.internal/*"],
        ),
        resolver=StaticAddressResolver({"demo.svc.internal": ["203.0.113.10"]}),
    )


def test_brokered_client_enforces_cap_non_cooperatively() -> None:
    import httpx

    from attacking_agent.adapters.brokered_http import BrokeredHttpClient
    from attacking_agent.domain.tools.contracts import SubrequestCapError

    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    broker = BrokeredHttpClient(client, _policy(), max_requests=2)
    broker.request("GET", "https://demo.svc.internal/a")
    broker.request("GET", "https://demo.svc.internal/b")
    try:
        broker.request("GET", "https://demo.svc.internal/c")
        raised = False
    except SubrequestCapError:
        raised = True
    assert raised  # cap enforced без кооперации инструмента


def test_brokered_client_blocks_disallowed_and_redirect() -> None:
    import httpx

    from attacking_agent.adapters.brokered_http import BrokeredHttpClient, BrokeredRequestDenied

    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path == "/r":
            return httpx.Response(302, headers={"location": "https://evil.example/x"})
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    broker = BrokeredHttpClient(client, _policy(), max_requests=10)

    try:
        broker.request("GET", "https://evil.example/collect")
        denied_direct = False
    except BrokeredRequestDenied:
        denied_direct = True
    assert denied_direct  # прямой запрос вне allowlist отклонён

    try:
        broker.request("GET", "https://demo.svc.internal/r")
        denied_redirect = False
    except BrokeredRequestDenied:
        denied_redirect = True
    assert denied_redirect  # redirect на запрещённый адрес отклонён
