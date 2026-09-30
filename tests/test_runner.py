import asyncio

import pytest

from agflow.graph.catalog import list_graphs, resolve
from agflow.graph.schema import coerce_inputs
from agflow.graph.store import RunStore, lookup_workspace
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
