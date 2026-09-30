"""agflow orchestrator MCP server — drives graphs from inside Antigravity.

The Antigravity main agent (the `orchestrator` custom agent or any agent using the `graph-run`
skill) loops::

    graph_start → graph_next → invoke_subagent(card.agent, card.subagent_prompt) ×N in parallel
                → graph_report(each result) → graph_next → ... → run finished

All state lives on disk in the workspace (``.agents/agflow/runs``), so the loop survives
server restarts, IDE reloads and hand-offs between agents.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from agflow import __version__, paths
from agflow.graph import catalog, engine
from agflow.graph.render import to_mermaid
from agflow.graph.schema import GraphError, coerce_inputs, parse_graph_text
from agflow.graph.store import RunNotFound, RunStore, lookup_workspace

INSTRUCTIONS = """\
agflow orchestrates multi-agent work as a graph. Loop: graph_start (once) -> graph_next -> dispatch
every returned card in parallel with invoke_subagent (agent = card.agent, prompt = card.subagent_prompt
verbatim) -> parse each subagent's final JSON block -> graph_report -> graph_next ... until the run
status is not 'running'. Human cards: ask the user card.task, then graph_report succeeded (approved)
or rejected. Never mark a node succeeded without evidence. Always pass the absolute workspace path."""

_workspaces: dict[str, Path] = {}


def _resolve_workspace(workspace: str | None, run_id: str | None = None) -> Path:
    if workspace:
        ws = Path(workspace).expanduser()
        if not ws.is_absolute():
            raise ValueError("workspace must be an absolute path")
        return ws.resolve()
    if run_id:
        if run_id in _workspaces:
            return _workspaces[run_id]
        found = lookup_workspace(run_id)
        if found:
            return found
    env = os.environ.get("AGFLOW_WORKSPACE")
    return Path(env).resolve() if env else paths.find_workspace()


def _store(workspace: str | None, run_id: str | None = None) -> RunStore:
    return RunStore(_resolve_workspace(workspace, run_id))


def _error(msg: str) -> dict[str, Any]:
    return {"ok": False, "error": msg}


def build_server() -> MCPServer:
    server = MCPServer("agflow", instructions=INSTRUCTIONS, version=__version__)

    @server.tool()
    def graph_list(workspace: str | None = None) -> dict[str, Any]:
        """List available orchestration graphs (workspace, installed plugins, global, bundled)."""
        ws = _resolve_workspace(workspace)
        return {"ok": True, "workspace": str(ws), "graphs": [
            {"id": r.id, "title": r.title, "description": r.description, "source": r.source,
             "path": str(r.path), **({"error": r.error} if r.error else {})}
            for r in catalog.list_graphs(ws)]}

    @server.tool()
    def graph_describe(graph: str, workspace: str | None = None) -> dict[str, Any]:
        """Show a graph's inputs, nodes and Mermaid diagram. `graph` is an id or a YAML path."""
        try:
            g, path = catalog.resolve(graph, _resolve_workspace(workspace))
        except GraphError as exc:
            return _error(str(exc))
        return {"ok": True, "path": str(path), "graph": g.model_dump(mode="json", exclude_defaults=True),
                "mermaid": to_mermaid(g)}

    @server.tool()
    def graph_validate(yaml_text: str) -> dict[str, Any]:
        """Validate graph YAML text without saving it. Returns errors or the Mermaid diagram."""
        try:
            g = parse_graph_text(yaml_text)
        except GraphError as exc:
            return _error(str(exc))
        return {"ok": True, "id": g.id, "nodes": len(g.nodes), "mermaid": to_mermaid(g)}

    @server.tool()
    def graph_start(graph: str, workspace: str, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
        """Start a run of `graph` (id or YAML path) in the absolute `workspace` path with `inputs`.

        Returns the run id and the next action. Call graph_next afterwards."""
        try:
            ws = _resolve_workspace(workspace)
            g, path = catalog.resolve(graph, ws)
            store = RunStore(ws)
            state = store.create(g, coerce_inputs(g, inputs), graph_path=str(path))
        except (GraphError, ValueError) as exc:
            return _error(str(exc))
        _workspaces[state.run_id] = ws
        return {"ok": True, "run_id": state.run_id, **_brief(state)}

    @server.tool()
    def graph_next(run_id: str, workspace: str | None = None, max_cards: int | None = None) -> dict[str, Any]:
        """Claim every ready node (up to the graph's max_parallel) and return dispatch cards.

        Dispatch ALL returned agent cards concurrently with invoke_subagent, using card.agent as the
        subagent and card.subagent_prompt verbatim as its prompt. Human cards: ask the user."""
        store = _store(workspace, run_id)
        try:
            with store.transaction(run_id) as state:
                g = state.graph_model()
                claimed = engine.claim(g, state, now=time.time(), limit=max_cards)
                cards = [engine.build_card(g, state, nid) for nid in claimed]
                view = engine.status_view(g, state)
        except RunNotFound:
            return _error(f"unknown run '{run_id}' in {store.workspace}")
        for c in cards:
            store.ledger(run_id, {"event": "dispatched", "node": c["node_id"], "agent": c["agent"],
                                  "attempt": c["attempt"]})
        return {"ok": True, "run_id": run_id, "status": view["status"], "cards": cards,
                "running": view["running"], "waiting_on_human": view["waiting_on_human"],
                "next_action": view["next_action"] if not cards else
                "Dispatch every card now (in parallel), then graph_report each result."}

    @server.tool()
    def graph_report(run_id: str, node_id: str, status: str, summary: str = "",
                     artifacts: list[str] | None = None, followups: list[str] | None = None,
                     workspace: str | None = None) -> dict[str, Any]:
        """Report a node's result. status: succeeded | failed (retryable) | rejected (verdict, no retry).

        Copy status/summary/artifacts from the subagent's final JSON block."""
        store = _store(workspace, run_id)
        try:
            with store.transaction(run_id) as state:
                g = state.graph_model()
                events = engine.report(g, state, node_id, status, now=time.time(), summary=summary,  # type: ignore[arg-type]
                                       artifacts=artifacts, followups=followups)
                view = engine.status_view(g, state)
                finished = state.status != "running"
                if finished:
                    store.write_report(state)
        except RunNotFound:
            return _error(f"unknown run '{run_id}' in {store.workspace}")
        except engine.EngineError as exc:
            return _error(str(exc))
        store.ledger(run_id, {"event": "reported", "node": node_id, "status": status,
                              "summary": summary[:500], "artifacts": artifacts or []})
        for e in events:
            store.ledger(run_id, e)
        return {"ok": True, "run_id": run_id, "events": events, "status": view["status"],
                "ready": view["ready"], "running": view["running"],
                "waiting_on_human": view["waiting_on_human"], "next_action": view["next_action"]}

    @server.tool()
    def graph_status(run_id: str | None = None, workspace: str | None = None,
                     verbose: bool = False) -> dict[str, Any]:
        """Status of a run (or the latest run in the workspace when run_id is omitted)."""
        store = _store(workspace, run_id)
        try:
            state = store.load(run_id) if run_id else (store.list() or [None])[0]
        except RunNotFound:
            state = None
        if state is None:
            return _error("no such run" if run_id else f"no runs in {store.workspace}")
        g = state.graph_model()
        view = engine.status_view(g, state, verbose=verbose)
        view["mermaid"] = to_mermaid(g, state)
        return {"ok": True, **view}

    @server.tool()
    def graph_runs(workspace: str | None = None, only_active: bool = False) -> dict[str, Any]:
        """List runs in the workspace (newest first)."""
        store = _store(workspace)
        runs = store.active() if only_active else store.list()
        return {"ok": True, "workspace": str(store.workspace), "runs": [
            {"run_id": r.run_id, "graph_id": r.graph_id, "status": r.status, "counts": engine.counts(r),
             "created_at": r.created_at} for r in runs[:50]]}

    @server.tool()
    def graph_resume(run_id: str, workspace: str | None = None) -> dict[str, Any]:
        """Recover a run after a crash/reload: running agent nodes go back to ready (no attempt lost)."""
        store = _store(workspace, run_id)
        try:
            with store.transaction(run_id) as state:
                g = state.graph_model()
                human = [nid for nid in state.nodes if g.node(nid).type == "human"]
                released = engine.release(g, state, [n for n in state.nodes if n not in human], now=time.time())
                view = engine.status_view(g, state)
        except RunNotFound:
            return _error(f"unknown run '{run_id}'")
        store.ledger(run_id, {"event": "resumed", "released": released})
        _workspaces[run_id] = store.workspace
        return {"ok": True, "released": released, **_brief_view(view)}

    @server.tool()
    def graph_abort(run_id: str, reason: str = "aborted by user", workspace: str | None = None) -> dict[str, Any]:
        """Abort a run. Pending/running nodes are marked blocked/failed."""
        store = _store(workspace, run_id)
        try:
            with store.transaction(run_id) as state:
                engine.abort(state, reason, now=time.time())
                store.write_report(state)
        except RunNotFound:
            return _error(f"unknown run '{run_id}'")
        store.ledger(run_id, {"event": "aborted", "reason": reason})
        return {"ok": True, "run_id": run_id, "status": "aborted"}

    @server.resource("agflow://graphs/{graph_id}", mime_type="text/markdown")
    def graph_resource(graph_id: str) -> str:
        """A graph definition with its Mermaid diagram."""
        g, path = catalog.resolve(graph_id, _resolve_workspace(None))
        return f"# {g.title or g.id}\n\n{g.description}\n\nSource: `{path}`\n\n```mermaid\n{to_mermaid(g)}```\n"

    @server.resource("agflow://runs/{run_id}", mime_type="text/markdown")
    def run_resource(run_id: str) -> str:
        """Live report of a run."""
        from agflow.graph.store import render_report_md

        state = _store(None, run_id).load(run_id)
        g = state.graph_model()
        return render_report_md(g, state) + f"\n```mermaid\n{to_mermaid(g, state)}```\n"

    return server


def _brief(state: engine.RunState) -> dict[str, Any]:
    return _brief_view(engine.status_view(state.graph_model(), state))


def _brief_view(view: dict[str, Any]) -> dict[str, Any]:
    return {k: view[k] for k in ("status", "ready", "running", "waiting_on_human", "next_action", "counts")}


def serve() -> None:
    build_server().run()
