"""Entrypoint tool-runner. Запуск: python -m services.tool_runner

Allowlist инструментов задаётся явно (здесь — эталонные sample_tools; в production
расширяется через конфигурацию). Произвольный код вне allowlist не исполняется.
"""

from __future__ import annotations

import os

import uvicorn

from services.tool_runner.app import create_runner_app

# Явный allowlist идентичностей инструментов (module, qualname).
_DEFAULT_ALLOWLIST: set[tuple[str, str]] = {
    ("attacking_agent.adapters.sample_tools", "echo_tool"),
    ("attacking_agent.adapters.http_tool", "HttpToolExecutor.__call__"),
}

app = create_runner_app(allowlist=_DEFAULT_ALLOWLIST)


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("AG_PORT", "8082")))  # noqa: S104


if __name__ == "__main__":
    main()
