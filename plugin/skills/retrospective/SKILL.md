---
name: retrospective
description: Look back at a finished or troubled workflow run, say what actually went wrong with evidence from its record, and turn problems that recur into proposed workflow changes the user can approve. Use at the end of a graph run, after retries, rework loops or evidence rejections, and when asked why a run was slow or failed or how a workflow could be better.
---

# Retrospective

A workflow improves when its recurring problems are written down with evidence and fixed in the workflow itself, not remembered by whoever happened to be watching. AG-Flow records lessons per workflow automatically when a run finishes. Your job is to read them honestly and propose changes. **You never change a workflow yourself: the user approves each change.**

## Procedure
1. **Open the record.** The report is at `.agents/agflow/runs/<run_id>/report.md` (`graph_status` gives the run id and path); the full event list is `ledger.jsonl` beside it. Read both.
2. **Read what the workflow already learned.** Call `graph_lessons` for the workflow: each pattern shows how many runs showed it.
3. **List what cost time or rework in this run**, one line each, with the step and the quoted cause from the record: a retry (why), a rework loop (what the gate said), an evidence rejection (what was missing), a timeout, a human rejection. If nothing did, say the run was clean and stop.
4. **Separate causes.**
   - *The workflow's fault*: a step's time limit too short, an acceptance criterion missing, an output nobody was told to write, a check that was advisory when the work needs it.
   - *Not the workflow's fault*: a flaky network, a one-off mistake, a missing credential. Report these; do not propose workflow changes for them.
5. **Check recurrence.** Propose a workflow change only for a problem seen in at least two runs. One occurrence is a note, not a rule.
6. **Create the proposal.** Run `agflow improve propose <graph id>`. It applies fixed rules to the recorded lessons and writes a proposal with a diff and the evidence; it changes no workflow. Then `agflow improve show <proposal id>` and read the diff before you describe it.
7. **Present it** to the user: each change, the evidence (runs, quotes), and what it costs (a longer time limit, one more retry, a stricter check). If the workflow is bundled, say that applying writes a workspace copy that stops following AG-Flow updates until it is removed.
8. **Wait for the answer.** Only on the user's explicit yes for that workflow, run `agflow improve apply <proposal id>` (add `--only c1` for a subset). `agflow improve reject <id>` records a no. `agflow improve undo <graph id>` reverts the last applied proposal.

## Suggestions the rules do not cover
If you see something the fixed rules cannot express (a prompt that is ambiguous, a missing verification step, a node that should run earlier), say so as a **suggestion**, mark it as your judgement rather than a recorded pattern, and offer to draft the graph change with the `graph-design` skill. Do not edit the graph file.

## Report
```
Run <id>: <succeeded|failed> - <one sentence>
Cost: <retries, rework loops, evidence rejections, timeouts - with steps>
Workflow problems (recurring): <pattern - runs - proposal id>
One-off problems: <list>
Proposed: <proposal id and its changes, or "nothing recurs often enough yet">
Needs your decision: apply / reject / leave for now
```
