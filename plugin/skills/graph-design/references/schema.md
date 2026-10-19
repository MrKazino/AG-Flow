# agflow graph schema

```yaml
id: my-workflow                 # required, ^[a-z][a-z0-9_-]{0,63}$
version: 1
title: Human readable title
description: What the workflow achieves
inputs:                         # values passed to graph_start / --input key=value
  feature: {type: string, required: true, description: "What to build"}
  has_ui:  {type: boolean, default: false}
  # types: string | boolean | number | integer
defaults:
  retries: 1                    # extra attempts after a `failed` report (0-10)
  timeout_min: 30               # lease per attempt; expired leases count as a failed attempt
  max_parallel: 4               # concurrent agent nodes
  evidence: advisory            # off | advisory | required: what a failed evidence check means (see "Evidence")
nodes:
  - id: spec                    # unique node id
    type: agent                 # agent (default) | gate | human
    title: Write the spec
    agent: architect            # required for agent nodes (custom agent name)
    skill: plan-spec            # optional: the subagent is told to use this skill
    prompt: |                   # Jinja2 (sandboxed, strict): inputs.*, nodes.<id>.{status,summary,artifacts,outputs}, run.{id,workspace}, node.{id,title,attempt}, feedback
      Write a spec for: {{ inputs.feature }}
    outputs: ["docs/specs/{{ run.id }}.md"]   # files (or folders, with a trailing /) the node must produce
    evidence: required          # overrides defaults.evidence for this node (agent nodes only)
    checks:                     # optional declarative checks on files; nothing is ever executed
      - {file: "docs/specs/{{ run.id }}.md", contains: ["## Acceptance"], not_contains: ["TODO"], min_bytes: 200}
    acceptance: ["Spec has testable acceptance criteria"]
    depends_on: []              # node ids, each at most once (a repeated id is rejected)
    when: null                  # Jinja expression; falsy => node skipped (downstream still runs)
    retries: null               # override defaults.retries
    timeout_min: null

  - id: review-gate
    type: gate                  # evaluated automatically once its dependencies finish
    depends_on: [review, security]
    require: all_succeeded      # or any_succeeded; skipped deps count as satisfied
    on_fail: {goto: implement, max_loops: 2}   # goto must be upstream of the gate

  - id: approve
    type: human
    prompt: "Ship it?"          # question shown to the user
    depends_on: [review-gate]
```

Template notes: `inputs.name` is the input called `name` even when that name is also a dict method (`items`, `keys`,
`values`, `get`, ...); the method is only used when there is no such input. Name inputs like node ids
(`^[a-z][a-z0-9_-]{0,63}$`): `agflow doctor` warns about any other name (`graph.input.name`) and agflow 0.5.0 will reject
it. Use `_` rather than `-`: `{{ inputs.bug-id }}` subtracts, only `inputs['bug-id']` reads the input.

## Evidence
When an agent node reports `succeeded`, the engine looks at the workspace before it believes it. It never runs anything;
it only reads files.

| Checked | `advisory` (default) | `required` |
|---|---|---|
| every `outputs` path exists inside the workspace and is not empty (a trailing `/` means a folder with something in it; `*` globs need one non-empty match) | warning | the report is refused |
| every reported artifact exists (`deleted: <path>` records a deletion; URLs and paths outside the workspace are not checked) | warning | the report is refused |
| every `checks` entry holds: the file exists, is at least `min_bytes`, contains every `contains` string and none of `not_contains` (literal text, case-sensitive; `file` and the strings are templates) | warning | the report is refused |
| the summary says something checkable (at least 20 characters, not just "done") | warning | the report is refused |
| the output was modified during this attempt; files the summary mentions in backticks exist | warning | warning |

A refused report becomes a failed attempt: the node goes back to ready (while retries remain) with feedback naming exactly
what is missing, and the ledger records `evidence_rejected`. `off` skips all checks. The dispatched task lists what will be
checked, so an agent can meet it the first time. Use `required` for any node whose value is a file, and a `verifier` node
(skill `verify-claims`) when the claim is a behaviour ("the tests pass") rather than a file.

## Node result statuses (reported by the orchestrator)
| status | meaning | engine behaviour |
|---|---|---|
| `succeeded` | acceptance criteria met, with evidence | node done |
| `failed` | could not complete | retried until `retries` exhausted, then failed |
| `rejected` | negative verdict (review/verification/human) | failed immediately; a downstream gate can trigger rework |

## Derived states
`skipped` (`when` was falsy) · `blocked` (an upstream node failed) · a run is `succeeded` when every node is succeeded/skipped, otherwise `failed` once nothing can progress.
