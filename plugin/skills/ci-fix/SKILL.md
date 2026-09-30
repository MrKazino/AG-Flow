---
name: ci-fix
description: Diagnose and fix failing CI pipelines (GitHub Actions, GitLab CI, CircleCI, Jenkins, etc.) — reproduce the failing job locally with the same versions and commands, find the root cause, fix it properly and prove it passes. Use when CI is red, a check fails on a PR, or a pipeline is slow or flaky.
---

# CI fix

## Procedure
1. **Read the failure.** Get the failing job's log (CI UI, `gh run view --log-failed`, or the log the user pasted). Find the *first* error, not the last line. Note job name, runner OS, language/tool versions, and the exact failing command.
2. **Classify:**
   - code/test failure caused by the change → fix the code or the test's legitimate expectation;
   - environment drift (tool/runtime/dependency version, missing secret, network) → pin or configure;
   - flaky test → treat as a real bug (`debug-systematic`), never as "just rerun";
   - infrastructure (runner died before steps ran) → one rerun is acceptable; a second failure is real.
3. **Reproduce locally** with the same command and versions (read the workflow file: `setup-*` versions, env vars, working directory, matrix entry). Use the project's lockfile. Confirm you see the same failure.
4. **Fix the root cause** with a minimal change. For workflow files: pin actions by version/SHA, use caches correctly (key on lockfile hash), set `timeout-minutes`, make steps idempotent, don't echo secrets.
5. **Prove it**: the failing command passes locally; run the rest of the job's commands too (lint, typecheck, tests). For flaky tests, loop the test many times.
6. **Never** disable, skip or quarantine a failing test, or weaken assertions/coverage thresholds, to get green — unless the user explicitly asks, and then say so prominently.

## Speed-ups (when asked to make CI faster)
Cache dependencies and build outputs; run independent jobs in parallel; split test suites; use path filters so docs-only changes skip heavy jobs; fail fast on lint before long tests.

## Exit criteria
Root cause in one or two sentences with evidence (log excerpt), the fix, and local proof (command + passing result). Note anything that can only be confirmed by CI.
