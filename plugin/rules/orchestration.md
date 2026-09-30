---
trigger: model_decision
description: Apply when running or designing multi-agent workflows — agflow graphs, parallel subagents, pipelines, or when the user mentions orchestration, graph_start, graph_next or a workflow run.
---

# Orchestration rules (agflow)

- The agflow MCP server is the source of truth for run state. Don't track node status in your head — call `graph_status`.
- Dispatch **all** cards returned by `graph_next` in parallel (one `invoke_subagent` per card). Use `card.agent` as the subagent and pass `card.subagent_prompt` verbatim — it already contains the task, inputs, upstream results, feedback, acceptance criteria and result contract.
- Report every card exactly once with `graph_report`, copying `status`, `summary` and `artifacts` from the subagent's final JSON block. If the block is missing or malformed, report `failed` with the reason.
- Status semantics: `succeeded` = acceptance criteria met with evidence; `rejected` = negative review/verification verdict (drives the gate's rework loop); `failed` = could not complete (retried automatically).
- Human cards are approvals: ask the user the question verbatim, wait, then report `succeeded` (approved) or `rejected`.
- Never edit files a running subagent owns. If two cards would touch the same files, report the conflict and let the graph serialise them.
- When a run ends, give the user a walkthrough: outcome, per-node summary, artifacts, follow-ups and the report path (`.agents/agflow/runs/<run_id>/report.md`).
- For a new kind of workflow, design a graph (skill `graph-design`), validate it with `graph_validate`, save it under `.agents/agflow/graphs/`, then run it.
