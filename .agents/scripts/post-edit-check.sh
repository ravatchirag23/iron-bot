#!/usr/bin/env bash
set -euo pipefail

# PostToolUse deterministic static check for Iron Bot (Antigravity 2.0):
# Resolves repo root cleanly regardless of CWD (.agents/ or project root),
# validates syntax immediately after edits, and emits {} per Antigravity PostToolUse contract.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

if [[ -x "$REPO_ROOT/venv/bin/python3" ]]; then
  PYTHON_BIN="$REPO_ROOT/venv/bin/python3"
else
  PYTHON_BIN="$(command -v python3)"
fi

PAYLOAD="$(cat || true)"

TARGET_FILE="$("$PYTHON_BIN" - "$REPO_ROOT" "$PAYLOAD" <<'PY'
import json, os, sys

repo_root = sys.argv[1]
raw = sys.argv[2] if len(sys.argv) > 2 else ""
if not raw.strip():
    sys.exit(0)
try:
    data = json.loads(raw)
except Exception:
    sys.exit(0)

def find_path(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in ("targetfile", "target_file", "filepath", "file_path", "path", "absolutepath", "absolute_path"):
                if isinstance(v, str) and v.strip():
                    return v.strip()
        for v in obj.values():
            res = find_path(v)
            if res:
                return res
    elif isinstance(obj, list):
        for item in obj:
            res = find_path(item)
            if res:
                return res
    return ""

p = find_path(data)
if not p:
    sys.exit(0)
if os.path.isabs(p):
    print(p)
else:
    cand = os.path.join(repo_root, p)
    print(cand if os.path.exists(cand) else p)
PY
)"

ERRORS=""
append_err() {
  local label="$1"
  local output="$2"
  ERRORS+="[${label}]"$'\n'"${output}"$'\n\n'
}

run_check() {
  local label="$1"
  shift
  local out
  if ! out=$("$@" 2>&1); then
    append_err "$label" "$out"
  fi
}

if [[ -n "$TARGET_FILE" && -f "$TARGET_FILE" ]]; then
  case "$TARGET_FILE" in
    *.py)
      if [[ -n "$PYTHON_BIN" ]]; then
        run_check "python syntax ($TARGET_FILE)" "$PYTHON_BIN" -m py_compile "$TARGET_FILE"
      fi
      if command -v ruff >/dev/null 2>&1; then
        run_check "ruff check ($TARGET_FILE)" ruff check "$TARGET_FILE"
      elif command -v flake8 >/dev/null 2>&1; then
        run_check "flake8 ($TARGET_FILE)" flake8 "$TARGET_FILE"
      fi
      if command -v mypy >/dev/null 2>&1 && ([[ -f "pyproject.toml" ]] || [[ -f "mypy.ini" ]]); then
        run_check "mypy ($TARGET_FILE)" mypy "$TARGET_FILE"
      fi
      ;;
    *.ts|*.tsx|*.js|*.jsx|*.mjs|*.cjs)
      if [[ -x "./node_modules/.bin/eslint" ]]; then
        run_check "eslint ($TARGET_FILE)" ./node_modules/.bin/eslint "$TARGET_FILE"
      elif command -v eslint >/dev/null 2>&1; then
        run_check "eslint ($TARGET_FILE)" eslint "$TARGET_FILE"
      fi
      if [[ -f "tsconfig.json" && -x "./node_modules/.bin/tsc" ]]; then
        run_check "tsc --noEmit" ./node_modules/.bin/tsc --noEmit
      fi
      ;;
    *.sh)
      run_check "bash -n ($TARGET_FILE)" bash -n "$TARGET_FILE"
      if command -v shellcheck >/dev/null 2>&1; then
        run_check "shellcheck ($TARGET_FILE)" shellcheck "$TARGET_FILE"
      fi
      ;;
    *.json)
      if [[ -n "$PYTHON_BIN" ]]; then
        run_check "json syntax ($TARGET_FILE)" "$PYTHON_BIN" -m json.tool "$TARGET_FILE"
      fi
      ;;
  esac
fi

if [[ -n "$ERRORS" ]]; then
  echo "$ERRORS" >&2
  echo '{}'
  exit 1
fi

echo '{}'
exit 0
