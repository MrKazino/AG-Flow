"""Headless graph execution: same engine and on-disk runs as the MCP orchestrator."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from agflow.graph import engine
from agflow.graph.engine import RunState
from agflow.graph.store import RunStore
from agflow.runner.backends import AgentBackend, NodeResult

EXIT_OK, EXIT_FAILED, EXIT_WAITING = 0, 1, 3

EventSink = Callable[[dict[str, Any]], None]


async def run_graph(store: RunStore, run_id: str, backend: AgentBackend, *, approve_human: bool = False,
                    on_event: EventSink | None = None) -> RunState:
    """Drive a run until it finishes or only human approvals remain."""
    emit = on_event or (lambda e: None)
    tasks: dict[asyncio.Task[NodeResult], str] = {}

    def log(event: dict[str, Any]) -> None:
        store.ledger(run_id, event)
        emit({"run_id": run_id, **event})

    try:
        while True:
            with store.transaction(run_id) as state:
                g = state.graph_model()
                if state.status != "running":
                    break
                claimed = engine.claim(g, state, now=time.time())
                cards = [engine.build_card(g, state, nid) for nid in claimed]
            for card in cards:
                log({"event": "dispatched", "node": card["node_id"], "agent": card["agent"],
                     "attempt": card["attempt"], "backend": backend.name})
                if card["type"] == "human":
                    if approve_human:
                        _report(store, run_id, card["node_id"],
                                NodeResult(status="succeeded", summary="auto-approved (--approve-human)"), log)
                    else:
                        log({"event": "waiting_on_human", "node": card["node_id"], "question": card["task"]})
                    continue
                tasks[asyncio.create_task(backend.run(card))] = card["node_id"]
            if not tasks:
                if cards and approve_human:
                    continue  # auto-approvals may have unblocked more nodes
                break
            done, _ = await asyncio.wait(tasks.keys(), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                nid = tasks.pop(task)
                try:
                    result = task.result()
                except Exception as exc:  # backend crash counts as a failed attempt
                    result = NodeResult(status="failed", summary=f"backend error: {type(exc).__name__}: {exc}")
                _report(store, run_id, nid, result, log)
    finally:
        if tasks:  # interrupted: cancel in-flight work and give the nodes back without burning attempts
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks.keys(), return_exceptions=True)
            with store.transaction(run_id) as state:
                engine.release(state.graph_model(), state, list(tasks.values()), now=time.time())
            log({"event": "interrupted", "released": list(tasks.values())})
    final = store.load(run_id)
    store.write_report(final)
    log({"event": "runner_stopped", "status": final.status})
    return final


def _report(store: RunStore, run_id: str, node_id: str, result: NodeResult, log: EventSink) -> None:
    """Record a node's result. The first report for a node wins.

    Whoever claimed a node owns its report (see the graph-run skill). If the engine refuses this one -- the node
    is no longer running because another driver (typically the MCP orchestrator) already settled it, or the run
    was aborted meanwhile -- the result is discarded and the conflict is logged; the run itself carries on.
    ``transaction()`` does not write state when its body raises, so a refused report changes nothing.
    """
    try:
        with store.transaction(run_id) as state:
            g = state.graph_model()
            events = engine.report(g, state, node_id, result.status, now=time.time(), summary=result.summary,
                                   artifacts=result.artifacts, followups=result.followups)
    except engine.EngineError as exc:
        log({"event": "report_conflict", "node": node_id, "status": result.status, "reason": str(exc)})
        return
    log({"event": "reported", "node": node_id, "status": result.status, "summary": result.summary[:500]})
    for e in events:
        log(e)


def approve(store: RunStore, run_id: str, node_id: str, *, approved: bool, note: str = "") -> list[dict[str, Any]]:
    with store.transaction(run_id) as state:
        g = state.graph_model()
        if g.node(node_id).type != "human":
            raise engine.EngineError(f"'{node_id}' is not a human approval node")
        events = engine.report(g, state, node_id, "succeeded" if approved else "rejected", now=time.time(),
                               summary=note or ("approved" if approved else "rejected"))
    store.ledger(run_id, {"event": "human_decision", "node": node_id, "approved": approved, "note": note})
    return events


def exit_code(state: RunState) -> int:
    if state.status == "succeeded":
        return EXIT_OK
    if state.status == "running":
        return EXIT_WAITING
    return EXIT_FAILED
