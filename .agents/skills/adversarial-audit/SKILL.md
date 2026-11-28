---
name: adversarial-audit
description: Runs an adversarial red-team audit on code changes to hunt logic flaws, boundary bugs, unhandled exceptions, concurrency issues, and security vulnerabilities, producing a numbered defect table and PASS/REJECT verdict. Use after implementation and before release verification.
---

# Skill: Adversarial Red-Team Audit Protocol

Assume the implementation under review contains hidden bugs until proven otherwise by concrete evidence and executed tests.

## Audit Procedure

1. **Scope & Diff Inspection**
   * Read the approved specification/plan and inspect the complete `git diff` plus surrounding untouched code that interacts with modified symbols.
   * Verify no protected files (`.agents/hooks.json`, `.agents/scripts/`, `tests/acceptance/`) were altered and no existing tests were weakened, skipped, or commented out.

2. **Six-Vector Defect Hunt**
   Systematically attack the diff across every vector:
   * **Logic & Invariants:** Off-by-one errors, inverted conditions, unreachable branches, broken state machines, incorrect operator precedence, silent fallbacks.
   * **Boundary & Input Abuse:** Empty strings, null/None, zero/negative values, huge inputs, duplicate keys, malformed encodings, timezone/DST/leap-year edges.
   * **Exception & Failure Paths:** Unhandled rejections/exceptions, partial state mutations on mid-flight failure, resource leaks, swallowed error codes.
   * **Concurrency & Ordering:** Race conditions, TOCTOU gaps, shared mutable state, missing locks/transactions, non-deterministic test timing.
   * **Security & Trust Boundaries:** Injection vectors, auth/authz bypasses, path traversal, secret exposure, unsafe deserialization.
   * **Regression & Contract Drift:** Broken callers, changed return types/status codes, missing migrations, undocumented config requirements.

3. **Adversarial Proof**
   * For every suspected defect, construct a concrete reproduction (failing edge-case test or exact input/command trace).
   * Execute relevant tests to verify whether the defect reproduces.

## Mandatory Output Format

Always conclude with the exact table and verdict structure below. Never omit columns or collapse multiple bugs into vague summaries.

### Defect Table

| # | File | Line | Severity | Reproduction | Required Fix |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | `path/to/file` | `42` | `CRITICAL \| HIGH \| MEDIUM \| LOW` | Exact input, test case, or execution trace triggering the flaw | Specific, actionable fix + required regression test |

*(If zero defects are found across all six vectors, output a single row stating `None — 0 defects found across all 6 audit vectors`.)*

### Verdict

* **Verdict:** `PASS` or `REJECT`
* **Gate Rule:** Any `CRITICAL`, `HIGH`, or `MEDIUM` defect—or any unverified assumption—MUST result in `REJECT`. `PASS` is permitted ONLY when the defect table has zero blocking defects and all adversarial checks succeeded.
