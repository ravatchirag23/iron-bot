# Engineering Constitution & Non-Negotiable Invariants

This workspace operates under a **Level 1000 Zero-Trust Verification Architecture**. No agent, subagent, or script output is trusted without deterministic, reproducible proof.

## Mandatory Execution Pipeline

Every non-trivial task MUST execute sequentially through all six stages. Skipping a stage is a policy violation:

1. **Spec** — Eliminate ambiguity, define explicit acceptance criteria, edge cases, and failure modes before writing code (use `/grill-me` when requirements are incomplete).
2. **Plan** — Produce a written, file-level implementation blueprint with explicit verification commands (use `/plan`) and obtain human approval before mutating production code.
3. **Isolated Implementation** — Execute approved changes via `builder-agent` (preferably in an isolated Git worktree / `branch` mode) with minimal, surgical diffs strictly scoped to the plan.
4. **Adversarial Red-Team Audit** — Invoke `red-team-auditor` in a fresh context window to actively hunt bugs, boundary failures, security flaws, and regressions, writing failing reproduction tests for any defect found.
5. **Deterministic Gate** — Pass every automated pre-edit, post-edit, and `Stop` verification check (syntax compilation, static checks, full `unittest` suite (`420+` tests), and live `/browser` verification for dashboard UIs) with real exit code `0`.
6. **Human Approval** — Invoke `gatekeeper-verifier` to compile a diff summary, real command outputs, and residual risk assessment for final human sign-off.

## Non-Negotiable Invariants

* **Zero Silent Fallbacks:** Never swallow exceptions, return fabricated default values to mask errors, or silently degrade behavior when a required dependency, input, or invariant fails. Fail loudly with actionable diagnostics.
* **Zero Skipped Tests:** Never mark tests as `.skip`, `@unittest.skip`, `@pytest.mark.skip`, `xit`, `xdescribe`, `t.Skip()`, or conditionally bypass assertions to make a suite pass.
* **Zero Commented-Out Assertions:** Never comment out, weaken, loosen tolerances on, or delete existing assertions or tests to force a green build.
* **Zero Unverified `PASS` Claims:** Never claim code compiles, passes lint, or passes tests unless you actually executed the exact command after the latest edit and observed exit code `0` in its output.
* **Zero Test or Gate Tampering:** Protected audit infrastructure (`.agents/hooks.json`, `.agents/scripts/`, and `tests/acceptance/`) is immutable to agents. Fix the implementation—never the grading rubric.
* **Zero Unscoped Changes:** Do not refactor unrelated modules, upgrade unrequested dependencies, or leave dead code, debug logs, or placeholder `TODO` stubs in committed work.
