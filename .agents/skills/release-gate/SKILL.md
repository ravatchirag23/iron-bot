---
name: release-gate
description: Executes the final pre-approval verification checklist requiring clean Python syntax compilation, zero lint or type errors, 100% passing unittest suite, and live browser verification for web UIs with zero console or network errors. Use before presenting work for final human approval.
---

# Skill: Release Gate Verification Protocol

Perform independent, command-backed verification before handing work to the human reviewer. Do not rely on claims from prior agents—re-verify directly from clean command outputs.

## Verification Checklist

Execute every applicable check and record the exact command and exit code:

1. **Workspace & Git Integrity**
   * Run `git status` and `git diff --stat` to confirm only planned, scoped files were changed.
   * Confirm zero skipped tests (`@unittest.skip`, `skip`, `xit`, `@pytest.mark.skip`, commented assertions) were introduced in the diff.
   * Confirm `.agents/hooks.json`, `.agents/scripts/`, and `tests/acceptance/` remain intact and untampered.

2. **Clean Build & Bytecode Compile**
   * Run `python3 -m compileall -q` across all Python source and test modules (plus any configured build command) and verify exit code `0`.

3. **Static Analysis, Formatting & Type Checking**
   * Run JSON validation on `.agents/hooks.json`, `bash -n` on `.agents/scripts/*.sh`, and any configured linters/typecheckers (`ruff`, `mypy`, `eslint`, `tsc --noEmit`).
   * Require zero errors and investigate every warning.

4. **Complete Automated Test Suite (100% Pass)**
   * Run `IRON_BOT_TEST_MODE=1 python3 -m unittest discover tests -v` (never a partial subset alone).
   * Confirm 100% pass rate with `0` failed, `0` errors, and `0` newly skipped tests.

5. **Live `/browser` Runtime Verification (Required if Dashboard/Web UI Modified)**
   * Start or inspect the dashboard server (`/status`, `/api/live_state`) using `/browser`.
   * Confirm **zero** browser console errors, **zero** uncaught page exceptions, and **zero** failed (`4xx`/`5xx`) network requests.

## Final Gatekeeper Report Format

Present a concise, evidence-backed summary for human sign-off:
* **Scope Summary:** Files added/modified/deleted and alignment with the approved plan.
* **Verification Matrix:** Table of every executed check, exact command, exit code, and key output metrics.
* **Audit Trail:** Confirmation of `red-team-auditor` `PASS` verdict and resolution of all previously flagged defects.
* **Residual Risk:** Any known environment limitations, manual rollout steps, or deferred low-severity items.
* **Release Recommendation:** `READY FOR HUMAN APPROVAL` or `BLOCKED` (with exact blocking reasons).
