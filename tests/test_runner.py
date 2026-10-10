import asyncio
import time

import pytest

from agflow.graph import engine
from agflow.graph.catalog import list_graphs, resolve
from agflow.graph.schema import coerce_inputs
from agflow.graph.store import RunStore, lookup_workspace
from agflow.runner import runner as runner_module
from agflow.runner.backends import FakeBackend, NodeResult, parse_result_text
from agflow.runner.runner import EXIT_FAILED, EXIT_OK, EXIT_WAITING, approve, exit_code, run_graph

INPUTS = {
    "feature-delivery": {"feature": "login", "has_ui": True},
    "bugfix": {"bug": "crash on empty input"},
    "security-sweep": {},
    "ui-feature": {"feature": "navbar"},
    "release": {"version": "1.0.0"},
}


def start(workspace, graph_id, inputs):
    g, p = resolve(graph_id, workspace)
    store = RunStore(workspace)
    return store, store.create(g, coerce_inputs(g, inputs), graph_path=str(p))


def test_bundled_graphs_listed(workspace):
    ids = {r.id for r in list_graphs(workspace)}
    assert set(INPUTS) <= ids


@pytest.mark.parametrize("graph_id", sorted(INPUTS))
def test_bundled_graph_runs_to_success(workspace, graph_id):
    store, state = start(workspace, graph_id, INPUTS[graph_id])
    backend = FakeBackend()
    final = asyncio.run(run_graph(store, state.run_id, backend, approve_human=True))
    assert final.status == "succeeded", final.nodes
    assert exit_code(final) == EXIT_OK
    assert (store.run_dir(state.run_id) / "report.md").is_file()
    assert lookup_workspace(state.run_id) == workspace.resolve()
    # every dispatched prompt rendered completely (no raw Jinja left)
    assert all("{{" not in c["subagent_prompt"] for c in backend.calls)


def test_human_wait_then_approve_and_resume(workspace):
    store, state = start(workspace, "bugfix", INPUTS["bugfix"])
    final = asyncio.run(run_graph(store, state.run_id, FakeBackend()))
    assert final.status == "succeeded"  # bugfix has no human node

    store, state = start(workspace, "release", INPUTS["release"])
    final = asyncio.run(run_graph(store, state.run_id, FakeBackend()))
    assert exit_code(final) == EXIT_WAITING
    approve(store, state.run_id, "approve", approved=True, note="go")
    final = asyncio.run(run_graph(store, state.run_id, FakeBackend()))
    assert final.status == "succeeded"
    assert final.nodes["approve"].summary == "go"


def test_rework_loop_with_scripted_results(workspace):
    store, state = start(workspace, "bugfix", INPUTS["bugfix"])
    backend = FakeBackend({"review": [{"status": "rejected", "summary": "fix is a band-aid"}]})
    final = asyncio.run(run_graph(store, state.run_id, backend))
    assert final.status == "succeeded"
    assert final.loops == {"gate": 1}
    fix_calls = [c for c in backend.calls if c["node_id"] == "fix"]
    assert len(fix_calls) == 2
    assert "band-aid" in fix_calls[1]["subagent_prompt"]


def test_backend_exception_is_a_failed_attempt(workspace):
    class Boom(FakeBackend):
        async def run(self, card):
            if card["node_id"] == "reproduce":
                raise RuntimeError("sdk crashed")
            return await super().run(card)

    store, state = start(workspace, "bugfix", INPUTS["bugfix"])
    final = asyncio.run(run_graph(store, state.run_id, Boom()))
    assert final.status == "failed"
    assert final.nodes["reproduce"].attempts == 2
    assert "sdk crashed" in final.nodes["reproduce"].error
    assert exit_code(final) == EXIT_FAILED


def test_interrupt_releases_running_nodes(workspace):
    store, state = start(workspace, "bugfix", INPUTS["bugfix"])

    async def go():
        task = asyncio.create_task(run_graph(store, state.run_id, FakeBackend(delay=5)))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    s = store.load(state.run_id)
    assert s.nodes["reproduce"].status.value == "pending"
    assert s.nodes["reproduce"].attempts == 0


def test_parse_result_text():
    text = 'did things\n```json\n{"status": "succeeded", "summary": "ok", "artifacts": ["a.py"]}\n```\n'
    assert parse_result_text(text) == NodeResult(status="succeeded", summary="ok", artifacts=["a.py"])
    assert parse_result_text("no json here").status == "failed"
    two = '```json\n{"status": "failed", "summary": "old"}\n```\n```json\n{"status": "rejected", "summary": "new"}\n```'
    assert parse_result_text(two).summary == "new"


# --- F2: a concurrent report (e.g. the MCP orchestrator) must not destroy the run --------------------------------


def test_concurrent_mcp_report_does_not_kill_the_run(workspace):
    store, state = start(workspace, "bugfix", INPUTS["bugfix"])

    class ReportedElsewhere(FakeBackend):
        """The orchestrator reports each node through the shared store while the runner is still working on it."""

        async def run(self, card):
            with store.transaction(state.run_id) as s:
                engine.report(s.graph_model(), s, card["node_id"], "succeeded", now=time.time(), summary="via MCP")
            return await super().run(card)

    events = []
    final = asyncio.run(run_graph(store, state.run_id, ReportedElsewhere(), on_event=events.append))
    assert final.status == "succeeded"
    assert not [n for n, ns in final.nodes.items() if ns.status.value == "running"]
    assert final.nodes["reproduce"].summary == "via MCP"  # the first report wins; the runner's own result is dropped
    conflicts = [e for e in events if e["event"] == "report_conflict"]
    assert conflicts and all("not running" in c["reason"] for c in conflicts)
    assert any(e.get("event") == "report_conflict" for e in store.read_ledger(state.run_id))  # visible, not swallowed


def test_human_auto_approve_conflict_is_tolerated(workspace, monkeypatch):
    store, state = start(workspace, "release", INPUTS["release"])
    real_report = runner_module._report

    def human_answers_first(store_, run_id, node_id, result, log):
        if node_id == "approve":
            approve(store_, run_id, "approve", approved=True, note="the human got there first")
        return real_report(store_, run_id, node_id, result, log)

    monkeypatch.setattr(runner_module, "_report", human_answers_first)
    events = []
    final = asyncio.run(run_graph(store, state.run_id, FakeBackend(), approve_human=True, on_event=events.append))
    assert final.status == "succeeded"
    assert final.nodes["approve"].summary == "the human got there first"
    assert [e for e in events if e["event"] == "report_conflict" and e["node"] == "approve"]


def test_report_to_an_aborted_run_is_a_logged_conflict_not_a_crash(workspace):
    store, state = start(workspace, "bugfix", INPUTS["bugfix"])

    class AbortedMidFlight(FakeBackend):
        async def run(self, card):
            with store.transaction(state.run_id) as s:
                engine.abort(s, "stopped from the dashboard", now=time.time())
            return await super().run(card)

    events = []
    final = asyncio.run(run_graph(store, state.run_id, AbortedMidFlight(), on_event=events.append))
    assert final.status == "aborted"
    assert [e for e in events if e["event"] == "report_conflict" and "aborted" in e["reason"]]


def test_unrelated_exceptions_still_stop_the_run(workspace, monkeypatch):
    """The guard is for EngineError only: anything else is a bug and must not be swallowed."""
    store, state = start(workspace, "bugfix", INPUTS["bugfix"])

    def broken(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(runner_module, "_report", broken)
    with pytest.raises(RuntimeError, match="disk on fire"):
        asyncio.run(run_graph(store, state.run_id, FakeBackend()))
