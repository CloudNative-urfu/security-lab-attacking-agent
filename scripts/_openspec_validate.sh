#!/usr/bin/env bash
# Валидация спецификаций OpenSpec. Использует standalone-бинарь, иначе npx через nix.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2

if command -v openspec >/dev/null 2>&1; then
  openspec validate --specs --strict && openspec validate --changes --strict 2>/dev/null
  exit $?
fi
if command -v nix >/dev/null 2>&1; then
  nix shell nixpkgs#nodejs_22 --command npx -y @fission-ai/openspec@2.1.0 validate --specs --strict
  exit $?
fi
echo "  ⚠ openspec не найден — пропуск валидации спек"
exit 0
