"""A workflow-centred view of a workspace: what each workflow needs, whether it is there, how its runs went, and what is
waiting for the user's decision.

The rest of AG-Flow is organised by kind of thing (skills, agents, MCP servers ...). A person thinks the other way
round: "can I run *feature-delivery* right now, and has it been going well?". This module answers that from the same
inventory the doctor reads, plus the workflow's own memory (:mod:`agflow.learn`) and its improvement proposals
(:mod:`agflow.improve`). It is **read-only**; the panel, the MCP tool ``graph_overview`` and ``agflow workflows`` show
what it returns.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from agflow import improve, learn
from agflow.graph import catalog, verify
from agflow.graph.schema import Graph, GraphError, load_graph
from agflow.graph.store import RunStore
from agflow.inventory import Inventory
from agflow.models import Item, Kind

ORCHESTRATOR_SERVER = "agflow"   # the MCP server that drives a workflow from inside Antigravity
RECENT_RUNS = 10
MAX_PATTERNS = 12


def _where(item: Item | None) -> dict[str, Any]:
    if item is None:
        return {"found": False, "enabled": None, "scope": None, "path": None}
    return {"found": True, "enabled": item.enabled, "scope": item.scope, "path": str(item.path)}


def needs(graph: Graph, inv: Inventory, workspace: Path) -> dict[str, Any]:
    """What running ``graph`` takes: its agents, skills, orchestrator server and files, each with where it was found."""
    agents: dict[str, dict[str, Any]] = {}
    skills: dict[str, dict[str, Any]] = {}
    files: list[dict[str, Any]] = []
    for node in graph.nodes:
        if node.agent:
            entry = agents.setdefault(node.agent, {**_where(inv.find(Kind.AGENT, node.agent, enabled=None)),
                                                   "nodes": [], "tools": []})
            entry["nodes"].append(node.id)
            item = inv.find(Kind.AGENT, node.agent, enabled=None)
            entry["tools"] = [str(t) for t in (item.meta.get("tools") or [])] if item else []
        if node.skill:
            skills.setdefault(node.skill, {**_where(inv.find(Kind.SKILL, node.skill, enabled=None)), "nodes": []})[
                "nodes"].append(node.id)
        mode = graph.evidence_for(node) if node.type == "agent" else "off"
        for out in node.outputs:
            files.append(_file_row(workspace, node.id, "output", out, mode))
        for check in node.checks:
            files.append(_file_row(workspace, node.id, "check", check.file, mode))
    server = inv.find(Kind.MCP, ORCHESTRATOR_SERVER, enabled=None)
    return {"agents": agents, "skills": skills, "mcp": {ORCHESTRATOR_SERVER: _where(server)}, "files": files}


def _file_row(workspace: Path, node: str, kind: str, path: str, mode: str) -> dict[str, Any]:
    templated = "{{" in path or "{%" in path
    exists = None
    if not templated:
        real = verify.within(workspace, path)
        exists = bool(real is not None and real.exists())
    return {"node": node, "kind": kind, "path": path, "templated": templated, "exists": exists, "evidence": mode}


def problems(graph: Graph, wanted: dict[str, Any]) -> list[dict[str, str]]:
    """What stands in the way of running ``graph``, from the most serious down."""
    found: list[dict[str, str]] = []

    def add(level: str, code: str, message: str) -> None:
        found.append({"level": level, "code": code, "message": message})

    for name, entry in wanted["agents"].items():
        if not entry["found"]:
            used = ", ".join(entry["nodes"])
            add("error", "workflow.agent.missing", f"agent '{name}' (used by {used}) is not installed")
        elif not entry["enabled"]:
            add("error", "workflow.agent.disabled", f"agent '{name}' is disabled: enable it on the Agents page")
    for name, entry in wanted["skills"].items():
        if not entry["found"]:
            used = ", ".join(entry["nodes"])
            add("warning", "workflow.skill.missing", f"skill '{name}' (used by {used}) is not installed")
        elif not entry["enabled"]:
            add("warning", "workflow.skill.disabled", f"skill '{name}' is disabled: enable it on the Skills page")
    server = wanted["mcp"][ORCHESTRATOR_SERVER]
    if not server["found"] or not server["enabled"]:
        add("warning", "workflow.mcp.orchestrator",
            "the agflow MCP server is not available, so a chat cannot drive this workflow "
            "(`agflow graph run` still can)")
    unenforced = sorted({f["node"] for f in wanted["files"] if f["kind"] == "output" and f["evidence"] != "required"})
    if unenforced:
        add("info", "workflow.evidence.advisory",
            f"declared outputs are only advisory at: {', '.join(unenforced)} (evidence: required would enforce them)")
    order = {"error": 0, "warning": 1, "info": 2}
    return sorted(found, key=lambda p: order[p["level"]])


def _runs(workspace: Path, graph_id: str) -> dict[str, Any]:
    rows = learn.load_runs(workspace, graph_id)
    done = [r for r in rows if r.get("status") in ("succeeded", "failed")]
    won = sum(1 for r in done if r.get("status") == "succeeded")
    recent = []
    for row in reversed(rows[-RECENT_RUNS:]):
        nodes = row.get("nodes") if isinstance(row.get("nodes"), dict) else {}
        recent.append({"run": row.get("run"), "status": row.get("status"), "at": row.get("at"),
                       "retried": sum(1 for n in nodes.values() if isinstance(n, dict) and n.get("attempts", 0) > 1)})
    return {"recorded": len(rows), "succeeded": won, "failed": len(done) - won,
            "success_rate": round(won / len(done), 3) if done else None, "recent": recent}


def proposal_row(workspace: Path, p: improve.Proposal, *, with_diff: bool = True) -> dict[str, Any]:
    row = {"id": p.id, "graph": p.graph, "status": improve.current_status(workspace, p), "created_at": p.created_at,
           "source": p.source, "target": p.target, "creates_copy": p.source != p.target,
           "changes": [{**c.to_json(), "text": c.describe()} for c in p.changes]}
    if with_diff:
        row["diff"] = p.diff
    return row


def _proposal_rows(workspace: Path, graph_id: str, *, with_diff: bool) -> list[dict[str, Any]]:
    return [proposal_row(workspace, p, with_diff=with_diff) for p in improve.proposals(workspace, graph_id)]


def _load(workspace: Path, graph_id: str) -> tuple[Graph | None, str, str, str]:
    """(graph, path, source label, error) for ``graph_id`` as the workspace currently sees it."""
    for ref in catalog.list_graphs(workspace):
        if ref.id == graph_id:
            if ref.error:
                return None, str(ref.path), ref.source, ref.error
            try:
                return load_graph(ref.path), str(ref.path), ref.source, ""
            except GraphError as exc:
                return None, str(ref.path), ref.source, str(exc)
    return None, "", "", f"unknown workflow '{graph_id}'"


def detail(workspace: Path, graph_id: str, *, inv: Inventory | None = None) -> dict[str, Any]:
    """Everything the panel shows about one workflow."""
    graph, path, source, error = _load(workspace, graph_id)
    if graph is None:
        return {"id": graph_id, "path": path, "source": source, "error": error, "problems": [
            {"level": "error", "code": "workflow.invalid", "message": error}]}
    inventory = inv if inv is not None else Inventory.discover(workspace)
    wanted = needs(graph, inventory, workspace)
    found = learn.patterns(learn.load_lessons(workspace, graph_id))[:MAX_PATTERNS]
    active = [r for r in RunStore(workspace).active() if r.graph_id == graph_id]
    def node_row(n: Any) -> dict[str, Any]:
        agent = n.type == "agent"
        return {"id": n.id, "type": n.type, "title": n.label, "agent": n.agent, "skill": n.skill,
                "depends_on": n.depends_on, "evidence": graph.evidence_for(n) if agent else None,
                "timeout_min": graph.timeout_for(n) if agent else None,
                "retries": graph.retries_for(n) if agent else None}

    return {
        "id": graph.id, "title": graph.title, "description": graph.description, "path": path, "source": source,
        "error": "", "nodes": [node_row(n) for n in graph.nodes],
        "inputs": {k: {"required": v.required, "type": v.type, "default": v.default, "description": v.description}
                   for k, v in graph.inputs.items()},
        "needs": wanted, "problems": problems(graph, wanted), "runs": _runs(workspace, graph_id),
        "active_runs": [r.run_id for r in active], "lessons": [p.to_json() for p in found],
        "proposals": _proposal_rows(workspace, graph_id, with_diff=True),
    }


def summary(workspace: Path, *, inv: Inventory | None = None, now: float | None = None) -> list[dict[str, Any]]:
    """One line per workflow: enough to see which need attention."""
    _ = time.time() if now is None else now
    inventory = inv if inv is not None else Inventory.discover(workspace)
    out = []
    for ref in catalog.list_graphs(workspace):
        row: dict[str, Any] = {"id": ref.id, "title": ref.title, "description": ref.description, "source": ref.source,
                               "path": str(ref.path), "error": ref.error}
        if ref.error:
            row.update(nodes=0, agents=[], skills=[], problems={"error": 1, "warning": 0, "info": 0},
                       runs=_runs(workspace, ref.id), proposals_pending=0, lessons=0, active=0)
            out.append(row)
            continue
        graph = load_graph(ref.path)
        wanted = needs(graph, inventory, workspace)
        counts = {"error": 0, "warning": 0, "info": 0}
        for p in problems(graph, wanted):
            counts[p["level"]] += 1
        row.update(nodes=len(graph.nodes), agents=sorted(wanted["agents"]), skills=sorted(wanted["skills"]),
                   problems=counts, runs=_runs(workspace, ref.id),
                   proposals_pending=sum(1 for p in improve.proposals(workspace, ref.id) if p.status == "pending"),
                   lessons=len(learn.patterns(learn.load_lessons(workspace, ref.id))),
                   active=sum(1 for r in RunStore(workspace).active() if r.graph_id == ref.id))
        out.append(row)
    return out
