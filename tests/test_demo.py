"""Смоук-тест сквозного демо-прогона."""

from __future__ import annotations

from attacking_agent.demo import run_demo


def test_demo_end_to_end_invariants() -> None:
    s = run_demo()
    assert s["episode_outcome"] == "SUPPORTED"
    assert s["stop_reason"] == "finished"
    assert s["steps_taken"] == 2
    assert s["closed_accepted"] is True
    assert s["tool_requests_consumed"] == "1"  # одна физическая отправка
    assert s["journal_result_recorded"] is True
    assert s["unresolved_unknowns"] == 0
    assert s["memory_publish_status"] == "published"
    assert s["memory_known_statements"]
    assert s["config_hash"].endswith("…")
