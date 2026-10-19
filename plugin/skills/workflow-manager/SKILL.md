---
name: workflow-manager
description: Show and manage the workspace's workflows (agflow graphs) - which ones can run now, what each needs, how its runs went, what it learned, and what improvements are waiting for approval - and open the AG-Flow Manager panel when it is switched on. Use when the user asks what workflows exist, which are healthy, why one keeps failing, how to improve one, or wants the manager.
---

# Workflow manager

Workflows are the unit people care about: "can I run *feature-delivery* now, and is it getting better?". This skill answers that from real data and never changes anything without a yes.

## Procedure
1. **Overview.** Call `graph_overview` (or `agflow workflows`). For each workflow it gives the number of steps, problems that stop it running (a missing or disabled agent, skill or the agflow MCP server), how many runs were remembered and how many succeeded, and any improvement proposals waiting.
2. **One workflow in depth.** `agflow workflows <graph id>` lists what each step needs and where it was found, the files it produces, recent runs and the lessons recorded. `graph_lessons` returns the lessons as data.
3. **Say what is wrong, in order:** errors first (the workflow cannot run), then warnings, then notes. Name the exact thing and where to fix it ("agent `verifier` is disabled: `agflow agents enable verifier`", or the Agents page in the manager).
4. **Improvements.** If a proposal is waiting, run `agflow improve show <id>` and explain each change with its evidence (how many runs showed the problem). Apply one only when the user says yes for that workflow: `agflow improve apply <id>` (add `--only c1` for part of it). `agflow improve reject <id>` records a no; `agflow improve undo <graph id>` reverts. If none is waiting but runs have gone badly, use the `retrospective` skill.
5. **The panel.** Run `agflow panel status`. Only when it says the AG-Flow Manager is **ready**, offer the link it prints: `[AG-Flow Manager](sidecar://agflow/manager/)` opens the manager in Antigravity's side pane. If it says the panel is installed but switched off, give the user the steps it prints; never switch a sidecar on yourself, and do not show the link until it is on.

## Report
Lead with the answer ("3 of 5 workflows are ready; `security-sweep` cannot run: agent `verifier` is disabled"), then the detail the user asked for, then the one next step you recommend.

## Rules
- Read-only unless the user approves a specific proposal.
- Quote numbers from the tools; do not estimate success rates or run counts.
- A workflow that has no remembered runs has no history yet: say that rather than calling it healthy or unhealthy.
