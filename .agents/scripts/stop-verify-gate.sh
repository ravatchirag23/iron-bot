#!/usr/bin/env bash
set -euo pipefail

# Stop deterministic verification gate for Iron Bot:
# Runs from REPO_ROOT regardless of CWD, verifies audit infrastructure, syntax compilation,
# anti-skip integrity, and executes the full 420-test unittest suite.
# Emits {"decision":"continue","reason":"..."} on ANY failure per Antigravity Stop contract.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

if [[ -x "$REPO_ROOT/venv/bin/python3" ]]; then
  PYTHON_BIN="$REPO_ROOT/venv/bin/python3"
else
  PYTHON_BIN="$(command -v python3)"
fi

PAYLOAD="$(cat || true)"

# Respect user cancellation or hard budget caps to prevent infinite stop loops
SHOULD_BYPASS="$("$PYTHON_BIN" - "$PAYLOAD" <<'PY'
import json, sys
raw = sys.argv[1] if len(sys.argv) > 1 else ""
if not raw.strip():
    print("0")
    sys.exit(0)
try:
    data = json.loads(raw)
    reason = str(data.get("terminationReason", "")).upper()
    if reason in ("USER_CANCELED", "MAX_INVOCATIONS", "MAX_FORCED_INVOCATIONS", "MAX_TOKEN_BUDGET_EXCEEDED"):
        print("1")
    else:
        print("0")
except Exception:
    print("0")
PY
)"

if [[ "$SHOULD_BYPASS" == "1" ]]; then
  echo '{}'
  exit 0
fi

FAILURES=""
CHECKS_RUN=0

record_failure() {
  local name="$1"
  local output="$2"
  FAILURES+="=== FAILED: ${name} ==="$'\n'"${output}"$'\n\n'
}

run_gate() {
  local name="$1"
  shift
  CHECKS_RUN=$((CHECKS_RUN + 1))
  local out
  if ! out=$("$@" 2>&1); then
    record_failure "$name" "$out"
  fi
}

# 0. Audit infrastructure integrity
if [[ -f ".agents/hooks.json" ]]; then
  run_gate "Validate .agents/hooks.json" "$PYTHON_BIN" -m json.tool .agents/hooks.json
fi
for script in .agents/scripts/*.sh; do
  if [[ -f "$script" ]]; then
    run_gate "Bash syntax ($script)" bash -n "$script"
  fi
done

# 1. Iron Bot Python compilation, anti-skip guard, and full unittest suite
if [[ -d "tests" || -f "requirements.txt" ]]; then
  # Check syntax across all repo .py files (skipping venv/.git/__pycache__)
  PY_FILES=()
  while IFS= read -r pyf; do
    PY_FILES+=("$pyf")
  done < <(find . -maxdepth 2 -name "*.py" -not -path "./venv/*" -not -path "./.venv/*" -not -path "./.git/*")
  if [[ ${#PY_FILES[@]} -gt 0 ]]; then
    run_gate "Python syntax compile check" "$PYTHON_BIN" -m py_compile "${PY_FILES[@]}"
  fi

  # Zero skipped-test policy guard
  if grep -rnE '@unittest\.skip|\.skip\(|@pytest\.mark\.skip' tests/ >/dev/null 2>&1; then
    record_failure "Anti-Skip Guard" "Skipped test decorator/call detected inside tests/. Remove all skips."
  fi

  # Optional linters/typecheckers if installed
  if command -v ruff >/dev/null 2>&1; then
    run_gate "ruff check ." ruff check .
  fi
  if command -v mypy >/dev/null 2>&1 && ([[ -f "pyproject.toml" ]] || [[ -f "mypy.ini" ]]); then
    run_gate "mypy ." mypy .
  fi

  # Full unittest discovery (runs all 420+ tests with Telegram test mode isolated)
  if find tests -maxdepth 2 -name "test_*.py" | grep -q .; then
    run_gate "python3 -m unittest discover tests" env IRON_BOT_TEST_MODE=1 "$PYTHON_BIN" -m unittest discover tests
  fi

  # Also run pytest if explicitly installed and pytest tests exist
  if command -v pytest >/dev/null 2>&1 && [[ -f "pytest.ini" || -f "pyproject.toml" ]]; then
    run_gate "pytest" env IRON_BOT_TEST_MODE=1 pytest
  fi
fi

# 2. Node.js / TypeScript detection (preserved if frontend added later)
if [[ -f "package.json" ]]; then
  PM="npm"
  if [[ -f "pnpm-lock.yaml" ]] && command -v pnpm >/dev/null 2>&1; then
    PM="pnpm"
  elif [[ -f "yarn.lock" ]] && command -v yarn >/dev/null 2>&1; then
    PM="yarn"
  fi
  if command -v "$PM" >/dev/null 2>&1; then
    if "$PYTHON_BIN" -c 'import json,sys; d=json.load(open("package.json")).get("scripts",{}); sys.exit(0 if d.get("test") and "no test specified" not in d.get("test") else 1)'; then
      run_gate "${PM} test" "$PM" test
    fi
  fi
fi

if [[ -n "$FAILURES" ]]; then
  "$PYTHON_BIN" - "$FAILURES" <<'PY'
import json, sys
err = sys.argv[1].strip()
if len(err) > 6000:
    err = err[:6000] + "\n... [truncated]"
print(json.dumps({
    "decision": "continue",
    "reason": f"Verification failed: {err}. Fix all failures before stopping."
}))
PY
  exit 0
fi

echo '{}'
exit 0
