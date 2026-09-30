---
name: docs-writer
description: Write or update developer documentation — READMEs, setup guides, API references, ADRs, runbooks and inline docstrings — that is accurate, task-oriented and verified against the code. Use when asked to document something, when behaviour or setup changed, or when onboarding docs are missing.
---

# Documentation writer

## Procedure
1. **Know the reader and task.** New contributor setting up? API consumer? On-call engineer? Write for what they need to *do*.
2. **Gather facts from the source of truth.** Read the code, config, scripts, CI files and existing docs. Never document behaviour you haven't verified.
3. **Pick the right format:**
   - README: what it is · quickstart (copy-paste commands that work) · configuration · common tasks · troubleshooting · contributing.
   - API reference: every public endpoint/function with parameters, types, defaults, errors and a runnable example.
   - ADR (`docs/adr/NNNN-title.md`): Context · Decision · Status · Consequences · Alternatives.
   - Runbook: symptoms · dashboards/queries · step-by-step mitigation · rollback · escalation.
4. **Write.** Lead with the most common task. Short sentences, active voice, second person. Use numbered steps for procedures, tables for options, fenced code with language tags.
5. **Verify.** Run every command/example you wrote (or state clearly that one is illustrative). Check links and anchors. Keep the table of contents in sync.
6. **Keep docs close to code**: update docstrings and comments alongside behaviour changes; link rather than duplicate.

## Checklist
- [ ] Quickstart works from a clean checkout.
- [ ] Every config option/env var documented with default and example.
- [ ] No stale names, paths or flags; no secrets in examples (use placeholders).
- [ ] Diagrams (Mermaid) where a picture beats a paragraph.

## Exit criteria
List documents created/updated and which examples/commands you executed to verify them.
