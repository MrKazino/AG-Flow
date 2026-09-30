---
name: coverage-gaps
description: Find and close the most important gaps in a test suite — untested risky logic, error paths, edge cases and missing regression tests — using coverage reports plus risk analysis rather than chasing line percentage. Use when asked to improve tests or coverage, before refactors, or when a bug escaped the tests.
---

# Coverage gaps

## Procedure
1. **Measure** with the project's tooling:
   - Python: `pytest --cov=<pkg> --cov-report=term-missing --cov-branch`
   - JS/TS: `npx vitest run --coverage` / `npx jest --coverage`
   - Go: `go test ./... -coverprofile=cover.out && go tool cover -func=cover.out`
   - Rust: `cargo llvm-cov`; JVM: JaCoCo
2. **Rank gaps by risk, not by lines.** Prioritise uncovered code that is: business-critical (money, auth, data integrity), complex (many branches), recently changed or bug-prone (`git log --format= --name-only | sort | uniq -c | sort -rn | head`), or error-handling paths.
3. **Check test quality where coverage is high**: are assertions meaningful? Would the tests fail if the logic were wrong? (Optionally mutate a line and see if a test fails, or run a mutation tool such as `mutmut`/`stryker` on a small module.)
4. **Write tests for the top gaps** following the `tdd` good-test rules: public interfaces, one behaviour per test, deterministic.
5. **Re-measure** and report the delta for the targeted modules.

## Rules
- Don't write assertion-free tests to inflate coverage.
- Don't test private implementation details or third-party code.
- If a gap is unreachable/dead code, propose deleting it instead of testing it.

## Exit criteria
Table of gaps addressed (module, risk reason, tests added), before/after coverage for those modules, full suite result.
