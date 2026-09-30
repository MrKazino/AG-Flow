---
trigger: model_decision
description: Apply when writing, modifying or reviewing source code — naming, structure, error handling, comments and consistency with the existing codebase.
---

# Code quality

## Consistency first
- Mirror the surrounding code: same naming scheme, file layout, import style, error-handling idiom and test style.
- Use the project's formatter and linter configuration; don't introduce a new style.

## Structure
- Functions do one thing; prefer early returns over nested conditionals.
- Keep modules cohesive; put new code where a maintainer would look for it.
- Don't abstract until there are two real call sites; don't leave unused parameters, flags or branches.

## Naming
- Names describe intent (`retry_budget`, not `n2`). Booleans read as predicates (`is_ready`, `has_access`).
- Avoid abbreviations that aren't already used in the codebase.

## Errors
- Validate at boundaries; inside the core, trust your types.
- Raise specific errors with actionable messages (what failed, with which input, what to do).
- Never catch-and-ignore. If you must swallow an error, log why.

## Comments and docs
- Comments explain *why*, constraints and non-obvious decisions — not what the next line does.
- Public APIs get docstrings with parameters, return values and failure modes.
- Update README/config docs when behaviour or setup changes.

## Before you hand off
- Remove debug prints, commented-out code and temporary files.
- Re-read your diff as a reviewer would: correctness, edge cases (empty, null, large, concurrent), naming, tests.
