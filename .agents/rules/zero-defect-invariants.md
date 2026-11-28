---
trigger: always_on
description: Core zero-defect engineering invariants enforcing strict error handling, boundary validation, edge-case coverage, and deterministic execution across all workspace files.
---

# Zero-Defect Engineering Invariants

These constraints apply to every turn, file edit, and subagent invocation without exception.

## 1. Strict Error Handling
* Validate all external boundaries (user input, API payloads, file I/O, environment variables, database responses) immediately upon entry.
* Propagate typed, contextual errors rather than swallowing exceptions with empty `catch`/`except` blocks.
* Never introduce silent fallback values (e.g., `|| []`, `or {}`, `default=0`) that mask corrupted state or missing required configuration.
* Ensure every resource acquired (files, sockets, locks, DB connections, subprocesses) is deterministically released on both success and error paths.

## 2. Input Validation & Boundary Safety
* Explicitly guard against null/undefined/None, empty collections, zero/negative numbers, integer overflow, NaN/Infinity, malformed UTF-8, oversized payloads, and unexpected enum values.
* Reject invalid state transitions explicitly with clear diagnostic messages rather than allowing partial mutations.

## 3. Edge-Case & Regression Coverage
* Every bug fix MUST include a regression test that fails before the fix and passes after it.
* Every new feature or public interface MUST include tests covering:
  1. Happy path
  2. Boundary/extreme values
  3. Invalid input / failure paths
  4. Idempotency or state-recovery behavior where applicable
* Never weaken, skip, or delete an existing test to make a build pass.

## 4. Deterministic Outputs
* Eliminate hidden nondeterminism: do not rely on unordered map iteration, unseeded randomness, wall-clock races, or arbitrary `sleep()` calls in tests or business logic.
* Sort serialized outputs and inject clocks/seeds as explicit dependencies so behavior is reproducible across machines and CI runs.
