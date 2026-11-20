#!/usr/bin/env bash
# Падает, если найдены секреты вне baseline.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
uv run detect-secrets scan \
  --exclude-files '\.venv/.*' \
  --exclude-files '\.git/.*' \
  --exclude-files 'uv\.lock' \
  --exclude-files '\.secrets\.baseline' > "$TMP" 2>/dev/null

# Сравниваем множество найденных файлов-с-секретами с baseline.
python3 - "$TMP" .secrets.baseline <<'PY'
import json, sys
new = json.load(open(sys.argv[1])).get("results", {})
base = json.load(open(sys.argv[2])).get("results", {})
def keyset(res):
    return {(f, s.get("type"), s.get("line_number")) for f, lst in res.items() for s in lst}
extra = keyset(new) - keyset(base)
if extra:
    print("Обнаружены новые потенциальные секреты вне baseline:")
    for f, t, ln in sorted(extra):
        print(f"  {f}:{ln} [{t}]")
    sys.exit(1)
PY
