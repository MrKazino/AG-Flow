---
name: graph-run
description: Drive an agflow orchestration graph end to end with the agflow MCP tools — start the run, dispatch every ready node to its subagent in parallel, report results, handle human approvals, rework loops and restarts, and finish with a walkthrough. Use when the user asks to run a workflow/graph (feature-delivery, bugfix, security-sweep, ui-feature, release or a custom one) or for any Complex multi-agent task.
---

# Run an agflow graph

## 1. Start
1. `graph_list(workspace=<abs path>)` → pick the graph. If unsure which fits, `graph_describe(graph)` shows inputs and nodes.
2. Extract the inputs from the user's request. Ask one concise question only if a **required** input is missing.
3. `graph_start(graph=<id>, workspace=<absolute workspace path>, inputs={...})` → remember `run_id`.

## 2. Dispatch loop
Repeat until the run status is not `running`:
1. `graph_next(run_id)` → `cards`.
2. For **each agent card**, call `invoke_subagent` with:
   - subagent: `card.agent`
   - prompt: `card.subagent_prompt` — pass it **verbatim** (it contains task, inputs, upstream results, feedback, acceptance criteria and the result contract).
   Launch all cards of the batch concurrently.
3. When a subagent returns, find its final fenced JSON block and call
   `graph_report(run_id, node_id=card.node_id, status, summary, artifacts, followups)`.
   - Missing/invalid JSON → report `failed` with "no result block" and the last lines of the reply.
   - Never upgrade a subagent's status. Never report `succeeded` without evidence in the summary.
4. For **human cards** (`card.type == "human"`): ask the user `card.task` verbatim, wait for the answer, report `succeeded` (approved) or `rejected` (with their reason as summary).
5. If `graph_next` returns no cards but nodes are still running, wait for the outstanding subagents and report them.

The server handles retries (`failed`), rework loops (a gate after `rejected` resets upstream nodes with feedback) and skips (`when` conditions). Just keep looping.

## 3. Recover
- Session restarted or unsure of state → `graph_runs(workspace, only_active=true)` → `graph_status(run_id)`.
- Subagents died mid-flight → `graph_resume(run_id)` returns running nodes to ready without burning attempts.
- User wants to stop → `graph_abort(run_id, reason)`.

## 4. Finish
`graph_status(run_id, verbose=true)` → walkthrough for the user:
- outcome (succeeded / failed / aborted) and why;
- per node: one line on what was done and its evidence;
- artifacts (files changed/created), follow-ups, rework loops used;
- report path `.agents/agflow/runs/<run_id>/report.md`.
If the run failed, say which node, the error, and propose the next step (fix and resume, or re-run).
