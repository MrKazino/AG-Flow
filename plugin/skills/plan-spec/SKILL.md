---
name: plan-spec
description: Turn a feature request or change into a precise, reviewable spec and implementation plan with testable acceptance criteria. Use before implementing any non-trivial feature, when asked to plan, design, scope or write a spec/RFC/PRD, or when requirements are vague.
---

# Plan & spec

A good spec lets someone else implement and verify the change without asking you questions.

## Procedure
1. **Explore first (10–20% of the effort).** Read the entry points and modules involved, their tests, and project docs. Note stack, conventions and constraints. Don't design in a vacuum.
2. **Frame the problem.** Who needs what and why; constraints (performance, compatibility, security, deadlines); explicit non-goals.
3. **Resolve ambiguity.** List open questions. Answer the ones the codebase can answer. Ask the user only those that change the design — one bundled message. For the rest, choose a sensible default and record it as an assumption.
4. **Design.** Prefer the smallest design that fits existing architecture. If there is a real trade-off, compare ≤3 options in a table (complexity, risk, performance, reversibility) and recommend one.
5. **Plan.** Ordered steps, each small enough to verify on its own, naming the files/functions to touch and the check that proves the step works.
6. **Acceptance criteria.** Each is observable and checkable by running something: a test, a command, an HTTP request, a browser flow. Avoid "works well", "is fast"; say "p95 < 200 ms for 1k items measured by X".
7. **Review your spec** against the checklist below, then save it.

## Spec template
Save to the path you were given, else `docs/specs/<yyyy-mm-dd>-<slug>.md`:

```markdown
# <Title>
## Summary            — 2–3 sentences
## Context            — current behaviour, why change
## Goals / Non-goals
## Design             — components, interfaces (signatures), data model & migrations,
                        errors, security, performance, observability
## Alternatives considered (if any)
## Implementation plan — numbered steps with files and verification per step
## Test plan          — unit / integration / e2e cases
## Acceptance criteria — checkbox list, each runnable
## Risks & rollout    — risks, feature flags, migration/rollback, open questions
```

## Checklist
- [ ] Every acceptance criterion can be verified by a command or scripted check.
- [ ] Files and APIs named actually exist (or are clearly marked new).
- [ ] Breaking changes, migrations and security-sensitive areas are called out.
- [ ] Non-goals prevent scope creep; assumptions are explicit.
- [ ] Steps are ordered so the build stays green after each.

## Exit criteria
Spec saved; summary lists the recommended approach, the step count, and open questions.
