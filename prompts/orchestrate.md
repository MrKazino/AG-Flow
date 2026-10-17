# Orchestrate prompt — run a feature through a multi-agent graph

Requires the agflow plugin (orchestrator MCP server + subagents). Select the **orchestrator** agent
if your Antigravity version lets you pick the primary agent; otherwise paste this into any agent.

---

```text
Run the agflow "<graph id: feature-delivery | bugfix | security-sweep | ui-feature | release>" graph
for this workspace.

Inputs:
- <input name>: <value>          # e.g. feature: "CSV export for the reports page"
- <input name>: <value>          # e.g. has_ui: true

Procedure (follow the graph-run skill):
1. graph_start with the absolute workspace path and the inputs above.
2. Loop: graph_next → invoke_subagent for EVERY card in parallel (card.agent, card.subagent_prompt
   verbatim) → parse each subagent's final JSON block → graph_report. Repeat until the run is not
   running.
3. For human approval cards, ask me the question verbatim and report my answer.
4. If anything is stuck twice, stop and show me the evidence and options.
5. When the run ends, give me the walkthrough from graph_status (verbose): outcome, what each node
   did with evidence, artifacts, follow-ups and the report path.
```

## Designing a new graph instead

```text
Use the graph-design skill to create an agflow graph for this repeatable workflow:
<describe the stages, who should do what, where reviews/approvals belong>.
Maximise safe parallelism, add a quality gate with a bounded rework loop after verification nodes,
and a human approval before anything outward-facing. Validate it with graph_validate, save it to
.agents/agflow/graphs/<id>.yaml, show me the Mermaid diagram, then ask whether to run it.
```
