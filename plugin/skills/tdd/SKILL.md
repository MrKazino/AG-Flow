---
name: tdd
description: Implement features and fixes test-first with the red-green-refactor loop, using the project's existing test framework and conventions. Use whenever writing or changing production code, fixing a bug, or when asked for TDD or "with tests".
---

# Test-driven development

## Loop (repeat per behaviour)
1. **Red** — write the smallest test that expresses the next behaviour. Run it and **see it fail for the right reason** (assertion, not import/syntax error).
2. **Green** — write the simplest code that makes it pass. Run the test; then the module's tests.
3. **Refactor** — improve names and structure with all tests green. No behaviour change in this step.

## Procedure
1. Detect the stack: test runner, command, config and location conventions:
   - Python: `pyproject.toml`/`pytest.ini` → `pytest -q path::test`
   - JS/TS: `package.json` scripts, `vitest`/`jest` config → `npx vitest run file` / `npx jest file`
   - Go: `go test ./pkg/... -run TestName`; Rust: `cargo test name`; Java: `mvn -q test -Dtest=Class#method` or `gradle test --tests`
2. List the behaviours to implement (from the spec's acceptance criteria), simplest first; include error paths and edge cases.
3. Run the loop per behaviour. Commit-sized steps; keep everything green between behaviours.
4. For **bug fixes**: first write a test reproducing the bug; show it failing; fix; show it passing.
5. Finish: run formatter, linter, typecheck and the full suite.

## Good tests
- Test through public interfaces; one behaviour per test; descriptive names.
- Use real collaborators where cheap; fake only boundaries (network, clock, randomness, slow I/O).
- Deterministic: no sleeps, no shared mutable state, no order dependence.
- Assert on outcomes the user cares about, not on internal calls.

## Anti-patterns to avoid
- Writing all tests after the code "to get coverage".
- Tests that pass before the implementation exists (they test nothing).
- Over-mocking so the test mirrors the implementation.
- Weakening assertions or adding `skip` to get green.

## Exit criteria
Report each behaviour with its test name, the red→green evidence for bug fixes, and the final full-suite result line.
