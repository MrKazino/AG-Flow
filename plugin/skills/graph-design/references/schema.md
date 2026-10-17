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
nodes:
  - id: spec                    # unique node id
    type: agent                 # agent (default) | gate | human
    title: Write the spec
    agent: architect            # required for agent nodes (custom agent name)
    skill: plan-spec            # optional: the subagent is told to use this skill
    prompt: |                   # Jinja2 (sandboxed, strict): inputs.*, nodes.<id>.{status,summary,artifacts,outputs}, run.{id,workspace}, node.{id,title,attempt}, feedback
      Write a spec for: {{ inputs.feature }}
    outputs: ["docs/specs/{{ run.id }}.md"]
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
(`^[a-z][a-z0-9_-]{0,63}$`): `agflow doctor` warns about any other name (`graph.input.name`) and agflow 0.4.0 will reject
it. Use `_` rather than `-`: `{{ inputs.bug-id }}` subtracts, only `inputs['bug-id']` reads the input.

## Node result statuses (reported by the orchestrator)
| status | meaning | engine behaviour |
|---|---|---|
| `succeeded` | acceptance criteria met, with evidence | node done |
| `failed` | could not complete | retried until `retries` exhausted, then failed |
| `rejected` | negative verdict (review/verification/human) | failed immediately; a downstream gate can trigger rework |

## Derived states
`skipped` (`when` was falsy) · `blocked` (an upstream node failed) · a run is `succeeded` when every node is succeeded/skipped, otherwise `failed` once nothing can progress.
