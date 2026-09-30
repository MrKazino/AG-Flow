---
name: debug-systematic
description: Find and fix the root cause of bugs, crashes, wrong output, flaky tests or regressions with a hypothesis-driven, evidence-based process (reproduce, isolate, bisect, fix, prevent). Use when something is broken or behaves unexpectedly, or after two failed fix attempts.
---

# Systematic debugging

Guessing is slow. Evidence is fast.

## Procedure
1. **Reproduce.** Get a reliable, minimal reproduction: exact command, input, environment, expected vs actual. Capture the full error and stack trace. If it's flaky, run it in a loop (e.g. 20×) and record the failure rate.
2. **Write the failing test** that captures the bug (see `tdd`). This is your oracle.
3. **Localise.** Read the stack trace bottom-up to the first frame in project code. Add targeted logging or use a debugger at boundaries. Narrow down with binary search:
   - over code: comment out / short-circuit halves of the path;
   - over history: `git bisect start; git bisect bad; git bisect good <sha>; git bisect run <test cmd>`;
   - over input: shrink the input until the bug disappears.
4. **Hypothesise.** Write 2–4 candidate causes ranked by likelihood. For each, name the observation that would confirm or refute it. Test the cheapest discriminating one first.
5. **Fix the cause, not the symptom.** Avoid catch-and-ignore, arbitrary sleeps, retries that mask races, or special-casing the failing input.
6. **Prove it.** Failing test now passes; run the related suite and the full suite. For flaky bugs, loop the test again (≥ 50×) with zero failures.
7. **Prevent.** Search for sibling occurrences of the same pattern (`grep`) and fix or report them. Remove temporary logging.

## Common root causes to check
Off-by-one and boundary conditions · null/None/undefined handling · stale caches · timezone/locale · floating-point comparison · unawaited promises / missing `await` · shared mutable state and race conditions · environment/config differences · dependency version drift · encoding (UTF-8/bytes) · incorrect assumptions about ordering.

## Stop rule
After two failed fixes, stop editing. Re-read the evidence, write down what you know vs assume, and gather new evidence before the next attempt.

## Exit criteria
Report: root cause (one or two sentences, with `file:line`), the fix, the regression test, and the before/after evidence.
