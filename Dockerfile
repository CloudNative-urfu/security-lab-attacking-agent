# Общий образ для сервисов харнеса (gateway, tool-runner, api, worker).
# Команда задаётся в docker-compose (python -m services.<name>).
FROM python:3.12-slim

# gVisor/seccomp/egress — слой изоляции конфигурируется на уровне оркестратора.
RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY services ./services

# Устанавливаем проект с нужными extras (API + LLM транспорт).
RUN uv sync --frozen --extra api --extra llm

ENV PYTHONPATH=/app
ENV PATH="/app/.venv/bin:${PATH}"

# По умолчанию — tool-runner; переопределяется в compose.
CMD ["python", "-m", "services.tool_runner"]
