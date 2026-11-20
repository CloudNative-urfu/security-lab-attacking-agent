#!/usr/bin/env bash
# Единые ворота качества и безопасности (engineering-policy).
# Запуск: scripts/check.sh  (локально и в CI — один и тот же набор).
# На NixOS ruff из wheel не запускается; скрипт сам выберет ruff через uv или nix.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 2

FAILED=()
run() { # run <имя> <команда...>
  local name="$1"; shift
  echo "▶ $name"
  if "$@"; then echo "  ✓ $name"; else echo "  ✗ $name"; FAILED+=("$name"); fi
}

# Выбор работающего ruff: uv-ruff, иначе nix.
pick_ruff() {
  if uv run ruff --version >/dev/null 2>&1; then echo "uv run ruff"; return; fi
  if command -v nix >/dev/null 2>&1; then echo "nix run nixpkgs#ruff --"; return; fi
  echo ""
}
RUFF="${RUFF_CMD:-$(pick_ruff)}"

if [ -n "$RUFF" ]; then
  # shellcheck disable=SC2086  # RUFF намеренно содержит несколько слов (напр. "nix run …")
  run "ruff (стиль+безопасность)" $RUFF check src tests
else
  echo "  ⚠ ruff не найден (ни uv, ни nix) — пропуск"; FAILED+=("ruff-missing")
fi

run "mypy (строгая типизация)"   uv run mypy src
run "bandit (безопасность)"      uv run bandit -r src -q
run "detect-secrets"             bash scripts/_detect_secrets.sh
run "pytest (тесты)"             uv run pytest -q
run "openspec (валидация спек)"  bash scripts/_openspec_validate.sh

# pip-audit требует сети; не валит ворота офлайн, но печатает статус.
if uv run pip-audit --progress-spinner off >/dev/null 2>&1; then
  echo "  ✓ pip-audit (аудит зависимостей)"
else
  echo "  ⚠ pip-audit: уязвимости или нет сети — проверьте вручную"
fi

echo "────────────────────────────────────────────"
if [ ${#FAILED[@]} -eq 0 ]; then
  echo "ВСЕ ВОРОТА ПРОЙДЕНЫ"
  exit 0
fi
echo "ПРОВАЛЕНЫ ВОРОТА: ${FAILED[*]}"
exit 1
