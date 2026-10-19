---
name: graph-run
description: Drive an agflow orchestration graph end to end with the agflow MCP tools — start the run, dispatch every ready node to its subagent in parallel, report results, handle human approvals, rework loops and restarts, and finish with a walkthrough. Use when the user asks to run a workflow/graph (feature-delivery, bugfix, security-sweep, ui-feature, release or a custom one) or for any Complex multi-agent task.
---

# Run an agflow graph

## Tool reference
This table is the one place the agflow MCP tools are specified; every other document names a tool without arguments
and points here. `?` marks an optional argument. Always pass `workspace` as an **absolute** path when you have one
(`graph_start` requires it); the other tools find a run's workspace from `run_id`.

| Tool | Signature | What it does |
|---|---|---|
| `graph_list` | `graph_list(workspace?)` | List the graphs available (workspace, installed plugins, global, bundled). |
| `graph_describe` | `graph_describe(graph, workspace?)` | Show one graph's inputs, nodes and diagram; `graph` is an id or a YAML path. |
| `graph_validate` | `graph_validate(yaml_text)` | Check graph YAML without saving it; returns the errors or the diagram. |
| `graph_start` | `graph_start(graph, workspace, inputs?)` | Start a run; returns the `run_id`. |
| `graph_next` | `graph_next(run_id, workspace?, max_cards?)` | Claim every ready node (up to the graph's `max_parallel`) and return dispatch cards. |
| `graph_report` | `graph_report(run_id, node_id, status, summary?, artifacts?, followups?, workspace?)` | Report a node's result: `succeeded`, `failed` (retried) or `rejected` (a verdict, no retry). |
| `graph_status` | `graph_status(run_id?, workspace?, verbose?)` | Status of a run, or of the latest run in the workspace when `run_id` is omitted. |
| `graph_runs` | `graph_runs(workspace?, only_active?)` | List the workspace's runs, newest first. |
| `graph_resume` | `graph_resume(run_id, workspace?)` | After a crash or reload: running agent nodes go back to ready without losing an attempt. |
| `graph_abort` | `graph_abort(run_id, reason?, workspace?)` | Abort a run; pending and running nodes are marked blocked or failed. |
| `graph_overview` | `graph_overview(workspace?)` | Every workflow at a glance: steps, what stops it running, run counts, waiting proposals; plus update/compatibility notes. Read-only. |
| `graph_lessons` | `graph_lessons(graph, workspace?, node?)` | What earlier runs of this workflow taught (recurring problems per step, run counts). Read-only. |

## 1. Start
1. `graph_list` with the absolute `workspace` → pick the graph. If unsure which fits, `graph_describe` shows its inputs and nodes.
2. Extract the inputs from the user's request. Ask one concise question only if a **required** input is missing.
3. `graph_start` with `graph` (the id), the absolute `workspace` and `inputs` → remember `run_id`.

## 2. Dispatch loop
Repeat until the run status is not `running`:
1. `graph_next` with the `run_id` → `cards`.
2. For **each agent card**, call `invoke_subagent` with:
   - subagent: `card.agent`
   - prompt: `card.subagent_prompt` — pass it **verbatim** (it contains task, inputs, upstream results, feedback, acceptance criteria and the result contract).
   Launch all cards of the batch concurrently.
3. When a subagent returns, find its final fenced JSON block and call `graph_report` with the `run_id`,
   `node_id` = `card.node_id`, and the block's `status`, `summary`, `artifacts` and `followups`.
   - Missing/invalid JSON → report `failed` with "no result block" and the last lines of the reply.
   - Never upgrade a subagent's status. Never report `succeeded` without evidence in the summary.
   - The engine checks `succeeded` reports against the workspace (declared outputs, listed artifacts, required content).
     If the answer carries an `evidence` block with `rejected: true`, the report was **not** accepted: the node is back
     in `ready` with feedback naming what is missing. Dispatch it again; do not report the same result a second time.
     `warnings` in that block are notes for your walkthrough.
4. For **human cards** (`card.type == "human"`): ask the user `card.task` verbatim, wait for the answer, report `succeeded` (approved) or `rejected` (with their reason as summary).
5. If `graph_next` returns no cards but nodes are still running, wait for the outstanding subagents and report them.

The server handles retries (`failed`), rework loops (a gate after `rejected` resets upstream nodes with feedback) and skips (`when` conditions). Just keep looping.

## 3. Recover
- Session restarted or unsure of state → `graph_runs` with `only_active` set → `graph_status` for the `run_id`.
- Subagents died mid-flight → `graph_resume` returns running nodes to ready without burning attempts.
- A subagent ended with an **infrastructure error** and no work (for example "There was a network issue connecting to the server" or "Agent execution terminated due to error") → that is not the node's verdict: do not report `failed`. Call `graph_resume`, then dispatch the node again. Report `failed` only when the subagent ran and could not finish.
- User wants to stop → `graph_abort` with the `run_id` and a `reason`.
- **One driver per run.** Whoever claimed a node owns its report. Don't also drive this run from `agflow graph run` (the headless runner): the first report for a node wins, and a later one is rejected with `node '<id>' is succeeded, not running` — treat that as "someone else already settled it", don't retry, and move on to `graph_next`.

## 4. Finish
`graph_status` with the `run_id` and `verbose` set → walkthrough for the user:
- outcome (succeeded / failed / aborted) and why;
- per node: one line on what was done and its evidence;
- artifacts (files changed/created), follow-ups, rework loops used;
- report path `.agents/agflow/runs/<run_id>/report.md`.
If the run failed, say which node, the error, and propose the next step (fix and resume, or re-run).
