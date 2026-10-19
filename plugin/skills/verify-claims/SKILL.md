---
name: verify-claims
description: Check what was claimed against what is true, by running the checks instead of reading the summary. Produces a claims ledger (claim, source, how it was checked, result) and a verified or rejected verdict. Use after implementation, before review or release, when a result is stated without proof, or when a subagent's report sounds more confident than its evidence.
---

# Verify claims

A claim is a sentence that can be true or false: "the tests pass", "the endpoint returns 404 for unknown ids", "this only touches `src/auth`". Your job is to find out which, and to say how you know.

## Procedure
1. **List the claims.** From the task, the upstream results, the diff and the final summary. One line each, with its source (who said it, where).
2. **Mark the load-bearing ones**: those the next step, the release or the user's decision depends on. Check those first, then as many others as time allows.
3. **Choose the cheapest direct check** (table below) and run it yourself. A direct check observes the thing itself; a summary, a changelog line or a green badge is not one.
4. **Record** claim, source, command or file opened, the decisive line of output, result.
5. **Rank** each as `verified`, `refuted` or `unverifiable`. Do not soften a refuted claim; do not upgrade an unverifiable one because it is plausible.
6. **Verdict.** `succeeded` only when every load-bearing claim is verified and none is refuted. Otherwise `rejected`, listing each problem claim with what you saw and what would settle it.

## Cheapest direct check
| Claim | Check |
|---|---|
| tests pass | run the exact command; read the counts; note skipped, xfail, deselected and collection errors |
| a file was created or changed | `git status`, `git diff --stat`; open it and read the part that matters |
| a function, flag or option exists | `grep_search` for the definition and for a caller; run it with `--help` or a minimal call |
| a bug is fixed | run the reproduction on the fixed code; check the regression test would fail without the fix (read the test, or run it against the base commit in a separate checkout) |
| nothing else is affected | find callers with `grep_search`; run their tests, not only the new one |
| performance improved | measure before and after with the same method and input; report both numbers |
| docs or examples are right | open the doc; run its commands in a scratch directory |
| a dependency or version | read the lockfile or manifest; run the tool's `--version` |
| an Antigravity or library behaviour | read the installed docs or source for that version; do not rely on memory |
| "done" with no detail | treat as unverifiable until a concrete artefact or output is named |

## Ledger format
Write it as a table the next reader can scan:

| # | Claim | Source | How checked | Result |
|---|---|---|---|---|
| 1 | 42 tests pass | implement summary | `pytest -q` → `42 passed, 1 skipped` | verified (1 skipped: not claimed) |
| 2 | `docs/api.md` documents `--force` | implement summary | opened the file; no `--force` | refuted |

End with a `Verdict:` line (`verified` or `rejected`) and the count per result.

## Pitfalls
- Re-running a failing test until it passes. Report the flake with the counts.
- Checking a different thing than was claimed (running one test when "the suite passes" was claimed).
- Trusting a path or name from the summary without opening it.
- Editing anything to make a check pass. You verify; the owner fixes.
