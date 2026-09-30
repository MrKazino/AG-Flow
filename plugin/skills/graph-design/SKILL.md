---
name: graph-design
description: Design a new agflow orchestration graph (YAML DAG of agent, gate and human nodes with parallel branches, conditions, retries and bounded rework loops) for a repeatable multi-agent workflow, validate it and save it to the workspace. Use when the user wants a custom workflow/pipeline, when no existing graph fits a Complex task, or when asked to automate a multi-step process with subagents.
---

# Design an agflow graph

Schema reference: `references/schema.md`. Existing graphs to copy from: `graph_list` → `graph_describe`.

## Procedure
1. **Model the workflow as outcomes.** Write the stages as verifiable results ("spec written", "tests pass", "security review has no high findings"), not activities.
2. **Map stages to subagents** (architect, implementer, tester, reviewer, security-auditor, ui-verifier, devops, researcher) and, where one applies, a skill.
3. **Maximise safe parallelism.** Nodes that don't depend on each other's output and don't edit the same files run in parallel (same `depends_on`). Serialise nodes that edit the same files.
4. **Add quality gates.** After review/verification nodes, add a `gate` with `on_fail: {goto: <upstream node to redo>, max_loops: 1–3}`. Reviewers/verifiers report `rejected` to trigger rework; the rework node receives their findings as feedback automatically.
5. **Add human approval** (`type: human`) before anything outward-facing or irreversible: deploy, publish, data migration, force changes.
6. **Make nodes precise.** Each agent node gets a `prompt` (Jinja: `{{ inputs.x }}`, `{{ nodes.<id>.summary }}`, `{{ run.id }}`), `outputs` (paths it should produce) and `acceptance` (checkable criteria). Use `when:` (a Jinja expression, e.g. `inputs.has_ui`) for optional branches.
7. **Validate**: `graph_validate(yaml_text)` (MCP) or `agflow graph validate file.yaml`. Fix every error: unknown dependencies, cycles, a rework target that isn't upstream of its gate, template syntax errors.
8. **Save** to `.agents/agflow/graphs/<id>.yaml`. Optionally `agflow graph compile <id>` to create a `/<id>` skill.
9. **Dry-run** headlessly with the fake backend to check the flow: `agflow graph run <id> --backend fake --approve-human -i key=value`.

## Design rules
- 3–10 nodes is typical; split giant graphs into smaller ones.
- Every path to the end passes through at least one verification node.
- Keep `max_parallel` ≤ 4 unless nodes are read-only.
- Rework loops are bounded (`max_loops`) — never design an unbounded cycle.

## Exit criteria
Saved graph path, the Mermaid diagram (from `graph_validate`), and a one-line description of each node.
