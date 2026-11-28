---
trigger: model_decision
description: Security, concurrency, dependency safety, and regression analysis rules. Activate whenever modifying authentication, I/O, network calls, database queries, shell commands, configuration, dependencies, or performing code audits.
---

# Security & Deep Audit Rules

Apply these checks rigorously during implementation and adversarial auditing.

## 1. Secrets & Credential Hygiene
* Never hardcode API keys, tokens, passwords, private keys, or connection strings in source code, tests, logs, or fixtures.
* Verify secrets are never leaked into stdout, error traces, analytics payloads, or client-side bundles.
* Confirm `.env` and credential files are excluded from version control.

## 2. Injection & Input Sanitization
* **SQL / NoSQL:** Use parameterized queries or bound statements exclusively; forbid string concatenation into queries.
* **Shell / Command Execution:** Avoid `shell=True`, `eval`, `exec`, or interpolated command strings. Pass argument arrays and validate allowlisted inputs.
* **Path Traversal:** Canonicalize and verify resolved paths remain strictly inside intended base directories before reading or writing.
* **Web / XSS / SSRF:** Escape untrusted output by context, enforce strict Content-Security-Policy where applicable, and validate outbound URLs against private/metadata IP ranges.

## 3. Concurrency & Race Conditions
* Inspect shared mutable state for TOCTOU (time-of-check to time-of-use) races, lost updates, deadlocks, and non-atomic read-modify-write sequences.
* Verify file writes use atomic rename patterns where partial writes could corrupt state.
* Verify async workflows handle cancellation, timeouts, retries with backoff, and idempotency keys safely.

## 4. Dependency & Supply-Chain Safety
* Do not add new third-party dependencies unless explicitly justified and approved in the plan.
* Verify pinned versions/lockfiles remain consistent and free of known critical vulnerabilities or unverified post-install scripts.

## 5. Regression & Blast-Radius Analysis
* Trace all callers, downstream consumers, schema contracts, and serialization formats affected by every modified symbol.
* Flag any breaking API, CLI, database schema, or config change and require explicit migration or backward-compatibility coverage.
