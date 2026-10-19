"""Pure graph state machine shared by the MCP orchestrator and the headless SDK runner.

No I/O happens here: callers load a :class:`RunState`, call these functions with an explicit
``now`` timestamp, and persist the result (see :mod:`agflow.graph.store`).

Node lifecycle::

    pending ──claim──► running ──report(succeeded)──► succeeded
       ▲                  │ report(failed) & attempts left
       └──────────────────┘ report(failed) & no attempts left / report(rejected) ──► failed
    pending ──(upstream failed/blocked)──► blocked
    pending ──(`when` is falsy)──► skipped
    gate: evaluated automatically once its dependencies are terminal; on failure it can
          reset an upstream node and everything downstream of it (bounded by max_loops).
"""

from __future__ import annotations

import secrets
import time
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from agflow.graph import templating
from agflow.graph.schema import Graph, Node, ancestors, descendants, topo_order


class Status(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


TERMINAL = {Status.SUCCEEDED, Status.FAILED, Status.SKIPPED, Status.BLOCKED}
SATISFIED = {Status.SUCCEEDED, Status.SKIPPED}
BROKEN = {Status.FAILED, Status.BLOCKED}

ReportStatus = Literal["succeeded", "failed", "rejected"]
RunStatus = Literal["running", "succeeded", "failed", "aborted"]

SUMMARY_MAX = 4000

RESULT_CONTRACT = """\
When you are done, end your reply with exactly one fenced JSON block:
```json
{"status": "succeeded | failed | rejected", "summary": "what you did and the evidence (<= 6 sentences)",
 "artifacts": ["files you created or changed"], "followups": ["optional next steps"]}
```
- "succeeded": every acceptance criterion is met and you have evidence (commands run, test output, screenshots).
- "rejected": you reviewed/verified work and it does NOT meet the bar; list the concrete required changes.
- "failed": you could not complete your task; say exactly what blocked you."""


class EngineError(ValueError):
    pass


class NodeState(BaseModel):
    status: Status = Status.PENDING
    attempts: int = 0
    summary: str = ""
    artifacts: list[str] = Field(default_factory=list)
    followups: list[str] = Field(default_factory=list)
    error: str = ""
    feedback: list[str] = Field(default_factory=list)
    started_at: float | None = None
    finished_at: float | None = None
    lease_expires_at: float | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)  # warnings from the evidence check of the accepted report


class RunState(BaseModel):
    run_id: str
    graph_id: str
    graph: dict[str, Any]
    graph_path: str | None = None
    workspace: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    status: RunStatus = "running"
    created_at: float
    updated_at: float
    nodes: dict[str, NodeState]
    loops: dict[str, int] = Field(default_factory=dict)
    stop_continues: int = 0
    progress_marker: int = 0
    note: str = ""

    def graph_model(self) -> Graph:
        return Graph.model_validate(self.graph)


def new_run_id(graph_id: str, now: float | None = None) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now or time.time()))
    return f"{graph_id}-{stamp}-{secrets.token_hex(2)}"


def new_run(graph: Graph, inputs: dict[str, Any], workspace: str, *, now: float,
            graph_path: str | None = None, run_id: str | None = None) -> RunState:
    state = RunState(
        run_id=run_id or new_run_id(graph.id, now),
        graph_id=graph.id,
        graph=graph.model_dump(mode="json"),
        graph_path=graph_path,
        workspace=workspace,
        inputs=inputs,
        created_at=now,
        updated_at=now,
        nodes={n.id: NodeState() for n in graph.nodes},
    )
    advance(graph, state, now=now)
    return state


# --- evaluation ---------------------------------------------------------------------------------


def _base_context(state: RunState) -> dict[str, Any]:
    return {"inputs": state.inputs, "run": {"id": state.run_id, "workspace": state.workspace}}


def _rendered_outputs(graph: Graph, base: dict[str, Any]) -> dict[str, list[str]]:
    return {n.id: [_safe_render(o, base) for o in n.outputs] for n in graph.nodes}


def _node_view(graph: Graph, state: RunState, outputs: dict[str, list[str]]) -> dict[str, Any]:
    nodes: dict[str, Any] = {}
    for n in graph.nodes:
        ns = state.nodes[n.id]
        nodes[n.id] = {
            "status": ns.status.value,
            "summary": ns.summary,
            "artifacts": list(ns.artifacts),
            "outputs": list(outputs[n.id]),
        }
    return nodes


def context(graph: Graph, state: RunState) -> dict[str, Any]:
    base = _base_context(state)
    return {**base, "nodes": _node_view(graph, state, _rendered_outputs(graph, base))}


class _PassContext:
    """The template context for the ``when`` conditions of one :func:`advance` pass.

    A node's rendered ``outputs`` depend only on the inputs and the run, so they are rendered once per pass instead of
    once per ``when`` (which compiled and rendered every node's templates again for every condition). The node view
    (status, summary, artifacts) is rebuilt for each evaluation because decisions taken earlier in the same pass change
    it. ``get()`` returns exactly what :func:`context` would at that moment.
    """

    def __init__(self, graph: Graph, state: RunState):
        self.graph, self.state = graph, state
        self._base = _base_context(state)
        self._outputs: dict[str, list[str]] | None = None

    def get(self) -> dict[str, Any]:
        if self._outputs is None:
            self._outputs = _rendered_outputs(self.graph, self._base)
        return {**self._base, "nodes": _node_view(self.graph, self.state, self._outputs)}


def _safe_render(template: str, ctx: dict[str, Any]) -> str:
    try:
        return templating.render(template, ctx)
    except Exception:  # outputs are hints; never fail the run on them
        return template


def render_text(template: str, ctx: dict[str, Any]) -> str:
    """Render a template string against a context from :func:`node_context` (a bad template renders as itself)."""
    return _safe_render(template, ctx)


def node_context(graph: Graph, state: RunState, node_id: str) -> dict[str, Any]:
    """The template context a node's own strings (prompt, outputs, checks) are rendered with."""
    ctx = context(graph, state)
    node, ns = graph.node(node_id), state.nodes[node_id]
    ctx["node"] = {"id": node.id, "title": node.label, "attempt": ns.attempts}
    ctx["feedback"] = list(ns.feedback)
    return ctx


def _set(ns: NodeState, status: Status, now: float, **fields: Any) -> None:
    ns.status = status
    for k, v in fields.items():
        setattr(ns, k, v)
    if status in TERMINAL:
        ns.finished_at = now
        ns.lease_expires_at = None


def advance(graph: Graph, state: RunState, *, now: float) -> list[dict[str, Any]]:
    """Resolve everything that can be decided without an agent: gates, skips, blocks, run status."""
    events: list[dict[str, Any]] = []
    if state.status == "aborted":
        return events
    order = topo_order(graph)
    when_context = _PassContext(graph, state)
    changed = True
    while changed:
        changed = False
        for nid in order:
            ns = state.nodes[nid]
            if ns.status != Status.PENDING:
                continue
            node = graph.node(nid)
            dep_status = {d: state.nodes[d].status for d in node.depends_on}
            if any(s not in TERMINAL for s in dep_status.values()):
                continue
            if node.type == "gate":
                events += _evaluate_gate(graph, state, node, dep_status, now)
                changed = True
                continue
            broken = [d for d, s in dep_status.items() if s in BROKEN]
            if broken:
                _set(ns, Status.BLOCKED, now, error=f"upstream failed: {', '.join(broken)}")
                events.append({"event": "blocked", "node": nid, "by": broken})
                changed = True
                continue
            if node.when:
                try:
                    ok = bool(templating.evaluate(node.when, when_context.get()))
                except Exception as exc:
                    _set(ns, Status.FAILED, now, error=f"`when` evaluation failed: {exc}")
                    events.append({"event": "failed", "node": nid, "error": ns.error})
                    changed = True
                    continue
                if not ok:
                    _set(ns, Status.SKIPPED, now, summary=f"skipped: `{node.when}` is false")
                    events.append({"event": "skipped", "node": nid})
                    changed = True
    _update_run_status(state, now)
    state.updated_at = now
    return events


def _evaluate_gate(graph: Graph, state: RunState, gate: Node, dep_status: dict[str, Status],
                   now: float) -> list[dict[str, Any]]:
    ns = state.nodes[gate.id]
    if gate.require == "any_succeeded":
        passed = any(s == Status.SUCCEEDED for s in dep_status.values())
    else:
        passed = all(s in SATISFIED for s in dep_status.values())
    if passed:
        _set(ns, Status.SUCCEEDED, now, summary="gate passed")
        return [{"event": "gate_passed", "node": gate.id}]
    failures = [d for d, s in dep_status.items() if s not in SATISFIED]
    feedback = [f"{d} ({state.nodes[d].status.value}): {state.nodes[d].summary or state.nodes[d].error}"
                for d in failures]
    used = state.loops.get(gate.id, 0)
    if gate.on_fail and used < gate.on_fail.max_loops and _rework_can_help(graph, state, gate, gate.on_fail.goto):
        state.loops[gate.id] = used + 1
        _reset_for_rework(graph, state, gate.on_fail.goto, now,
                          [f"Rework requested by gate '{gate.id}' (loop {used + 1}/{gate.on_fail.max_loops}):",
                           *feedback])
        return [{"event": "rework", "gate": gate.id, "goto": gate.on_fail.goto, "loop": used + 1}]
    _set(ns, Status.FAILED, now, error="gate failed: " + "; ".join(feedback))
    return [{"event": "gate_failed", "node": gate.id, "failures": failures}]


def _rework_can_help(graph: Graph, state: RunState, gate: Node, goto: str) -> bool:
    """Rework only re-runs `goto` and its descendants; failures elsewhere (or of `goto` itself,
    whose retries are already spent) can't be fixed by it."""
    redoable = descendants(graph, goto)
    return all(state.nodes[nid].status != Status.FAILED or nid in redoable for nid in ancestors(graph, gate.id))


def _reset_for_rework(graph: Graph, state: RunState, goto: str, now: float, feedback: list[str]) -> None:
    for nid in {goto} | descendants(graph, goto):
        ns = state.nodes[nid]
        if ns.status != Status.PENDING or ns.attempts:
            ns.history.append(_snapshot_attempt(ns))
        state.nodes[nid] = NodeState(history=ns.history, feedback=feedback if nid == goto else [])


def _snapshot_attempt(ns: NodeState) -> dict[str, Any]:
    return {"status": ns.status.value, "attempts": ns.attempts, "summary": ns.summary, "error": ns.error,
            "finished_at": ns.finished_at}


def _update_run_status(state: RunState, now: float) -> None:
    if state.status == "aborted":
        return
    statuses = [ns.status for ns in state.nodes.values()]
    if all(s in TERMINAL for s in statuses):
        state.status = "failed" if any(s in BROKEN for s in statuses) else "succeeded"
    else:
        state.status = "running"


# --- dispatch -----------------------------------------------------------------------------------


def ready(graph: Graph, state: RunState) -> list[str]:
    """Pending nodes whose dependencies are all satisfied (call after :func:`advance`)."""
    if state.status != "running":
        return []
    out = []
    for nid in topo_order(graph):
        ns = state.nodes[nid]
        node = graph.node(nid)
        if ns.status == Status.PENDING and node.type != "gate" and all(
            state.nodes[d].status in SATISFIED for d in node.depends_on
        ):
            out.append(nid)
    return out


def claim(graph: Graph, state: RunState, *, now: float, limit: int | None = None) -> list[str]:
    """Mark up to ``max_parallel`` ready nodes as running and return their ids."""
    recover_stale(graph, state, now=now)
    advance(graph, state, now=now)
    running_agents = sum(1 for nid, ns in state.nodes.items()
                         if ns.status == Status.RUNNING and graph.node(nid).type == "agent")
    capacity = max(0, graph.defaults.max_parallel - running_agents)
    if limit is not None:
        capacity = min(capacity, limit)
    claimed = []
    for nid in ready(graph, state):
        node = graph.node(nid)
        if node.type == "agent":
            if capacity <= 0:
                continue
            capacity -= 1
        ns = state.nodes[nid]
        ns.status = Status.RUNNING
        ns.attempts += 1
        ns.started_at = now
        ns.error = ""
        ns.lease_expires_at = None if node.type == "human" else now + graph.timeout_for(node) * 60
        claimed.append(nid)
    state.updated_at = now
    return claimed


def report(graph: Graph, state: RunState, node_id: str, status: ReportStatus, *, now: float,
           summary: str = "", artifacts: list[str] | None = None, followups: list[str] | None = None,
           evidence_problems: list[str] | None = None,
           evidence_notes: list[str] | None = None) -> list[dict[str, Any]]:
    """Record a node's result. ``evidence_problems`` / ``evidence_notes`` come from :mod:`agflow.graph.verify` (the
    engine does no I/O): a ``succeeded`` report with problems is turned into a failed attempt whose feedback names
    them, so the retry knows exactly what to fix."""
    if node_id not in state.nodes:
        raise EngineError(f"unknown node '{node_id}'")
    if state.status == "aborted":
        raise EngineError(f"run {state.run_id} was aborted")
    ns = state.nodes[node_id]
    if ns.status != Status.RUNNING:
        raise EngineError(f"node '{node_id}' is {ns.status.value}, not running; call graph_next to claim it first")
    if status not in ("succeeded", "failed", "rejected"):
        raise EngineError(f"status must be succeeded, failed or rejected, got {status!r}")
    summary = (summary or "").strip()[:SUMMARY_MAX]
    node = graph.node(node_id)
    events: list[dict[str, Any]] = []
    if status == "succeeded" and evidence_problems:
        reason = ("evidence check failed: " + "; ".join(evidence_problems))[:1500]
        events.append({"event": "evidence_rejected", "node": node_id, "problems": list(evidence_problems)})
        events += _fail_attempt(graph, state, node, now, reason, retryable=node.type == "agent",
                                artifacts=artifacts, followups=followups)
    elif status == "succeeded":
        _set(ns, Status.SUCCEEDED, now, summary=summary, artifacts=list(artifacts or []),
             followups=list(followups or []), feedback=[], evidence=list(evidence_notes or [])[:10])
        events.append({"event": "succeeded", "node": node_id})
        if evidence_notes:
            events.append({"event": "evidence_warning", "node": node_id, "notes": list(evidence_notes)[:10]})
    else:
        events += _fail_attempt(graph, state, node, now, summary or f"reported {status}", retryable=(
            status == "failed" and node.type == "agent"), artifacts=artifacts, followups=followups)
    events += advance(graph, state, now=now)
    return events


def _fail_attempt(graph: Graph, state: RunState, node: Node, now: float, reason: str, *, retryable: bool,
                  artifacts: list[str] | None = None, followups: list[str] | None = None) -> list[dict[str, Any]]:
    ns = state.nodes[node.id]
    max_attempts = 1 + graph.retries_for(node)
    if retryable and ns.attempts < max_attempts:
        ns.history.append({**_snapshot_attempt(ns), "status": "failed", "error": reason})
        ns.status = Status.PENDING
        ns.lease_expires_at = None
        ns.error = reason
        ns.feedback = [f"Attempt {ns.attempts}/{max_attempts} failed: {reason}. Fix the cause before retrying."]
        return [{"event": "retry", "node": node.id, "attempt": ns.attempts}]
    _set(ns, Status.FAILED, now, summary=reason, error=reason, artifacts=list(artifacts or ns.artifacts),
         followups=list(followups or []))
    return [{"event": "failed", "node": node.id, "error": reason}]


def recover_stale(graph: Graph, state: RunState, *, now: float) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for nid, ns in state.nodes.items():
        if ns.status == Status.RUNNING and ns.lease_expires_at is not None and ns.lease_expires_at < now:
            node = graph.node(nid)
            events += _fail_attempt(graph, state, node, now,
                                    f"no report within {graph.timeout_for(node)} min (lease expired)",
                                    retryable=True)
    if events:
        events += advance(graph, state, now=now)
    return events


def release(graph: Graph, state: RunState, node_ids: list[str] | None = None, *, now: float) -> list[str]:
    """Put running nodes back to pending without consuming an attempt (e.g. after a crash)."""
    released = []
    for nid, ns in state.nodes.items():
        if ns.status == Status.RUNNING and (node_ids is None or nid in node_ids):
            ns.status = Status.PENDING
            ns.attempts = max(0, ns.attempts - 1)
            ns.lease_expires_at = None
            released.append(nid)
    advance(graph, state, now=now)
    return released


def abort(state: RunState, reason: str, *, now: float) -> None:
    for ns in state.nodes.values():
        if ns.status in (Status.RUNNING, Status.PENDING):
            _set(ns, Status.FAILED if ns.status == Status.RUNNING else Status.BLOCKED, now,
                 error=f"aborted: {reason}")
    state.status = "aborted"
    state.note = reason
    state.updated_at = now


# --- views ------------------------------------------------------------------------------------------


def build_card(graph: Graph, state: RunState, node_id: str, *, lessons: list[str] | None = None) -> dict[str, Any]:
    """Everything an orchestrator needs to dispatch one node to a subagent. ``lessons`` are short sentences from
    earlier runs of this workflow (see :mod:`agflow.learn`); the engine only carries them."""
    node = graph.node(node_id)
    ns = state.nodes[node_id]
    ctx = context(graph, state)
    ctx["node"] = {"id": node.id, "title": node.label, "attempt": ns.attempts}
    ctx["feedback"] = list(ns.feedback)
    task = templating.render(node.prompt, ctx).strip() if node.prompt else (
        f"Carry out the '{node.label}' step of the '{graph.id}' workflow.")
    outputs = [_safe_render(o, ctx) for o in node.outputs]
    upstream = [{"id": d, "status": state.nodes[d].status.value, "summary": state.nodes[d].summary,
                 "artifacts": state.nodes[d].artifacts} for d in node.depends_on]
    card: dict[str, Any] = {
        "run_id": state.run_id,
        "node_id": node.id,
        "type": node.type,
        "title": node.label,
        "agent": node.agent,
        "skill": node.skill,
        "attempt": ns.attempts,
        "max_attempts": 1 + graph.retries_for(node),
        "timeout_min": graph.timeout_for(node),
        "task": task,
        "expected_outputs": outputs,
        "acceptance": list(node.acceptance),
        "upstream": upstream,
        "feedback": list(ns.feedback),
        "lessons": list(lessons or []),
        "evidence": {
            "mode": graph.evidence_for(node) if node.type == "agent" else "off",
            "checks": [{"file": _safe_render(c.file, ctx), "contains": [_safe_render(s, ctx) for s in c.contains],
                        "not_contains": [_safe_render(s, ctx) for s in c.not_contains], "min_bytes": c.min_bytes}
                       for c in node.checks],
        },
    }
    card["subagent_prompt"] = compose_subagent_prompt(graph, state, card)
    return card


def compose_subagent_prompt(graph: Graph, state: RunState, card: dict[str, Any]) -> str:
    if card["type"] == "human":
        return card["task"]
    parts = [f"# Task: {card['title']}  (workflow `{graph.id}`, run `{state.run_id}`, "
             f"attempt {card['attempt']}/{card['max_attempts']})", "", card["task"]]
    if card["skill"]:
        parts += ["", f"Use the `{card['skill']}` skill for this task and follow its procedure."]
    if state.inputs:
        parts += ["", "## Inputs"] + [f"- {k}: {v}" for k, v in state.inputs.items()]
    if card["upstream"]:
        parts += ["", "## Upstream results"]
        for u in card["upstream"]:
            arts = f" (artifacts: {', '.join(u['artifacts'])})" if u["artifacts"] else ""
            parts.append(f"- {u['id']} [{u['status']}]: {u['summary'] or '-'}{arts}")
    if card["feedback"]:
        parts += ["", "## Feedback you must address"] + [f"- {f}" for f in card["feedback"]]
    if card.get("lessons"):
        parts += ["", "## Lessons from earlier runs of this workflow"] + [f"- {s}" for s in card["lessons"]]
    if card["expected_outputs"]:
        parts += ["", "## Expected outputs"] + [f"- {o}" for o in card["expected_outputs"]]
    if card["acceptance"]:
        parts += ["", "## Acceptance criteria"] + [f"- [ ] {a}" for a in card["acceptance"]]
    parts += _evidence_section(card)
    parts += ["", f"Workspace: {state.workspace}", "", RESULT_CONTRACT]
    return "\n".join(parts)


def _evidence_section(card: dict[str, Any]) -> list[str]:
    """Tell the agent exactly what the engine will check when it reports, so it can meet the check the first time."""
    ev = card.get("evidence") or {}
    mode = ev.get("mode", "off")
    if mode == "off" or card["type"] != "agent":
        return []
    head = ("a failed check sends this task back to you with the reason" if mode == "required"
            else "findings are recorded as warnings")
    lines = ["", f"## Evidence the engine checks when you report ({head})"]
    for out in card["expected_outputs"]:
        lines.append(f"- `{out}` " + ("is a folder with at least one file in it" if out.endswith(("/", "\\"))
                                      else "exists in the workspace and is not empty"))
    for chk in ev.get("checks", []):
        lines.append(f"- `{chk['file']}` exists")
        if chk.get("min_bytes"):
            lines.append(f"  - is at least {chk['min_bytes']} bytes")
        lines += [f"  - contains {s!r}" for s in chk.get("contains", [])]
        lines += [f"  - does not contain {s!r}" for s in chk.get("not_contains", [])]
    lines += ["- every file you list under `artifacts` exists in the workspace "
              "(write `deleted: <path>` for a deletion)",
              "- your summary names what you did and the evidence (commands run, results), not just \"done\""]
    return lines


def counts(state: RunState) -> dict[str, int]:
    out = {s.value: 0 for s in Status}
    for ns in state.nodes.values():
        out[ns.status.value] += 1
    return out


def status_view(graph: Graph, state: RunState, *, verbose: bool = False) -> dict[str, Any]:
    running = [nid for nid, ns in state.nodes.items() if ns.status == Status.RUNNING]
    waiting_human = [nid for nid in running if graph.node(nid).type == "human"]
    rdy = ready(graph, state)
    if state.status != "running":
        next_action = f"Run {state.status}. Write the final walkthrough for the user (graph_status has details)."
    elif rdy:
        next_action = "Call graph_next to claim the ready nodes and dispatch them to subagents in parallel."
    elif waiting_human:
        next_action = (f"Ask the user to approve {', '.join(waiting_human)} and report their answer with "
                       "graph_report (succeeded = approved, rejected = not approved).")
    elif running:
        next_action = "Wait for the running subagents, then graph_report each result."
    else:
        next_action = "Nothing is claimable; inspect node errors."
    nodes = []
    for n in graph.nodes:
        ns = state.nodes[n.id]
        entry: dict[str, Any] = {"id": n.id, "type": n.type, "agent": n.agent, "status": ns.status.value,
                                 "attempts": ns.attempts}
        text = ns.summary or ns.error
        if text:
            entry["summary"] = text if verbose else text[:280]
        if verbose and ns.artifacts:
            entry["artifacts"] = ns.artifacts
        nodes.append(entry)
    return {
        "run_id": state.run_id,
        "graph_id": state.graph_id,
        "status": state.status,
        "workspace": state.workspace,
        "inputs": state.inputs,
        "counts": counts(state),
        "ready": rdy,
        "running": [r for r in running if r not in waiting_human],
        "waiting_on_human": waiting_human,
        "rework_loops": state.loops,
        "next_action": next_action,
        "nodes": nodes,
    }


def progress_marker(state: RunState) -> int:
    """Monotonic-ish number that changes whenever the run makes progress (used by the Stop hook)."""
    return sum(ns.attempts + len(ns.history) + (ns.status in TERMINAL) for ns in state.nodes.values())
