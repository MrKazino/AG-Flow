---
name: code-review
description: Review a diff, branch or pull request like a principal engineer — correctness, security, performance, API compatibility, tests, readability — and return ranked, actionable findings with an approve or request-changes verdict. Use when asked to review code or a PR, or before merging any change.
---

# Code review

## Procedure
1. **Scope.** Get the diff: `git diff <base>...HEAD --stat` then the full diff; or the PR's changed files. Read the spec/issue to know what the change must do.
2. **Context.** Read each changed file fully and the callers/callees one level out. Understand the before and after.
3. **Verify, don't trust.** Run the tests, linter and typecheck. Run the specific scenario when feasible.
4. **Walk the checklist** (below). Note each finding with location and a concrete fix.
5. **Rank** findings: blocker → major → minor/nit. Deduplicate; group by theme.
6. **Verdict.** Approve only when there are no blockers/majors and checks pass.

## Checklist
**Correctness** — does it meet the spec? Edge cases (empty, null, huge, unicode, concurrent)? Error paths handled? Off-by-one, integer overflow, float comparison, timezone? Resource cleanup (files, connections, locks)?
**Security** — input validation, injection (SQL/shell/template/path), authz on every entry point, secrets in code/logs, unsafe deserialisation, SSRF, XSS/CSRF, dependency risk.
**Concurrency** — shared state, races, missing awaits, deadlocks, idempotency and retries.
**Performance** — N+1 queries, unbounded loops/memory, work in hot paths, missing pagination/indexes.
**API & compatibility** — breaking changes to public APIs, DB schemas, config, CLI flags; migrations reversible; versioning.
**Tests** — would the tests fail without the change? Are important cases covered? Deterministic?
**Readability** — names, function size, duplication, comments that explain why, consistency with codebase conventions.
**Ops** — logging/metrics for new paths, feature flags, docs and changelog updated.

## Writing findings
`path/to/file.ext:42 — [blocker] <problem in one line>. Why it matters: <impact>. Fix: <specific change or code snippet>.`

Be kind and precise; don't nitpick style the formatter handles; don't demand patterns the codebase doesn't use.

## Exit criteria
Verdict (approve / request changes), counts per severity, the ranked findings, and the checks you ran with results.
