---
name: refactor-safely
description: Restructure code without changing behaviour — extract, rename, split modules, remove duplication, modernise APIs — in small verified steps protected by tests. Use when asked to refactor, clean up, reorganise, reduce duplication or tech debt, or before a change that the current structure makes hard.
---

# Safe refactoring

Refactoring changes structure, never behaviour. Every step keeps the tests green.

## Procedure
1. **Define the target.** What will be better (readability, coupling, testability, performance) and how you'll know. Keep scope tight.
2. **Secure a safety net.** Run the existing tests for the area. If coverage is thin, add **characterisation tests** that pin current behaviour (including odd behaviour) before changing anything.
3. **Plan small steps.** Each step is one mechanical transformation: rename, extract function/class, inline, move, change signature (with adapter), replace conditional with polymorphism, etc.
4. **Execute step by step.** After each step: run the tests; if red, revert that step rather than debugging forward. Prefer IDE/language-server refactors and `grep` to find every usage (including strings, configs, docs, reflection/dynamic imports).
5. **Separate concerns.** Never mix refactoring with behaviour changes or formatting churn in the same step/commit.
6. **Public APIs.** Keep backward compatibility with deprecation shims unless the task explicitly allows breaking changes; document them.
7. **Finish.** Full test suite, linter, typecheck. Diff review: no accidental behaviour change, no leftover dead code.

## Red flags — stop and reconsider
- Tests needed changes in their assertions (behaviour changed).
- The diff keeps growing beyond the stated target.
- You can't explain the benefit in one sentence.

## Exit criteria
Report the transformations applied, before/after structure (brief), and the test results after the final step.
