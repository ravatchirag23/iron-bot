#!/usr/bin/env bash
set -euo pipefail

# PreToolUse deterministic gate for Antigravity:
# Reads PreToolUse JSON from stdin and denies any write/edit targeting
# protected audit infrastructure (.agents/hooks.json, .agents/scripts/, tests/acceptance/).

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PAYLOAD="$(cat || true)"

python3 - "$REPO_ROOT" "$PAYLOAD" <<'PY'
import json
import os
import posixpath
import sys

repo_root = os.path.realpath(sys.argv[1])
raw = sys.argv[2] if len(sys.argv) > 2 else ""
DENY_MSG = "Protected audit/test file cannot be modified by agent."

def deny(reason: str = DENY_MSG) -> None:
    print(json.dumps({"decision": "deny", "reason": reason}))
    sys.exit(0)

def allow() -> None:
    print(json.dumps({"decision": "allow"}))
    sys.exit(0)

if not raw.strip():
    allow()

try:
    data = json.loads(raw)
except Exception:
    deny("Protected gate error: malformed PreToolUse JSON payload.")

def extract_paths(obj):
    paths = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = k.lower()
            if kl in ("targetfile", "target_file", "filepath", "file_path", "path", "absolutepath", "absolute_path", "destination"):
                if isinstance(v, str) and v.strip():
                    paths.append(v.strip())
            else:
                paths.extend(extract_paths(v))
    elif isinstance(obj, list):
        for item in obj:
            paths.extend(extract_paths(item))
    return paths

candidate_paths = extract_paths(data)
cwd = os.path.realpath(os.getcwd())

PROTECTED_EXACT = {
    ".agents/hooks.json",
}
PROTECTED_PREFIXES = (
    ".agents/scripts/",
    "tests/acceptance/",
)

def strip_leading_dot_slash(p: str) -> str:
    while p.startswith("./"):
        p = p[2:]
    return p

def check_rel(rel_path: str) -> bool:
    rel = strip_leading_dot_slash(rel_path.replace("\\", "/"))
    if rel in PROTECTED_EXACT:
        return True
    for pref in PROTECTED_PREFIXES:
        if rel == pref.rstrip("/") or rel.startswith(pref):
            return True
    return False

def is_protected(path_str: str) -> bool:
    p = path_str.replace("\\", "/")

    # Check both direct relative interpretation and resolution against REPO_ROOT and CWD
    if not os.path.isabs(p):
        norm_rel = posixpath.normpath(p)
        if check_rel(norm_rel):
            return True
        for base in (repo_root, cwd):
            real_p = os.path.realpath(os.path.join(base, p))
            try:
                rel = os.path.relpath(real_p, repo_root).replace("\\", "/")
                if check_rel(rel):
                    return True
            except ValueError:
                pass
    else:
        real_p = os.path.realpath(p)
        try:
            rel = os.path.relpath(real_p, repo_root).replace("\\", "/")
            if check_rel(rel):
                return True
        except ValueError:
            pass

    norm_any = posixpath.normpath(p)
    if norm_any.endswith("/.agents/hooks.json") or norm_any == "hooks.json" and os.path.basename(cwd) == ".agents":
        return True
    if "/.agents/scripts/" in norm_any or norm_any.endswith("/.agents/scripts"):
        return True
    if "/tests/acceptance/" in norm_any or norm_any.endswith("/tests/acceptance"):
        return True

    return False

for path_str in candidate_paths:
    if is_protected(path_str):
        deny(DENY_MSG)

allow()
PY
