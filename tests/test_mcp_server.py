import json
import sys

import pytest
from mcp import Client, StdioServerParameters

from agflow.mcp_server import build_server
from tests.conftest import write

GRAPH = """
id: mini
inputs: {goal: {type: string, required: true}}
defaults: {max_parallel: 2}
nodes:
  - {id: plan, agent: architect, prompt: "Plan {{ inputs.goal }}"}
  - {id: build, agent: implementer, depends_on: [plan]}
  - {id: test, agent: tester, depends_on: [plan]}
  - {id: review, agent: reviewer, depends_on: [build, test]}
  - {id: gate, type: gate, depends_on: [review], on_fail: {goto: build, max_loops: 1}}
  - {id: ok, type: human, prompt: "Ship {{ inputs.goal }}?", depends_on: [gate]}
"""


def data(result):
    if getattr(result, "structured_content", None):
        return result.structured_content
    return json.loads(result.content[0].text)


async def call(client, tool, **args):
    return data(await client.call_tool(tool, args))


@pytest.fixture
def ws_with_graph(workspace, graphs_dir):
    write(graphs_dir / "mini.yaml", GRAPH)
    return workspace


async def test_full_orchestration_loop(ws_with_graph):
    ws = str(ws_with_graph)
    async with Client(build_server()) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        assert {"graph_start", "graph_next", "graph_report", "graph_status", "graph_resume"} <= names

        listed = await call(c, "graph_list", workspace=ws)
        assert "mini" in {g["id"] for g in listed["graphs"]}

        bad = await call(c, "graph_start", graph="mini", workspace=ws, inputs={})
        assert bad["ok"] is False and "goal" in bad["error"]

        started = await call(c, "graph_start", graph="mini", workspace=ws, inputs={"goal": "search"})
        run_id = started["run_id"]
        assert started["ready"] == ["plan"]

        nxt = await call(c, "graph_next", run_id=run_id)
        assert [card["node_id"] for card in nxt["cards"]] == ["plan"]
        assert nxt["cards"][0]["task"] == "Plan search"
        assert "subagent_prompt" in nxt["cards"][0]

        not_running = await call(c, "graph_report", run_id=run_id, node_id="build", status="succeeded")
        assert not_running["ok"] is False

        await call(c, "graph_report", run_id=run_id, node_id="plan", status="succeeded", summary="planned")
        nxt = await call(c, "graph_next", run_id=run_id)
        assert sorted(card["node_id"] for card in nxt["cards"]) == ["build", "test"]
        for card in nxt["cards"]:
            await call(c, "graph_report", run_id=run_id, node_id=card["node_id"], status="succeeded", summary="ok")

        nxt = await call(c, "graph_next", run_id=run_id)
        await call(c, "graph_report", run_id=run_id, node_id="review", status="rejected", summary="needs tests")
        status = await call(c, "graph_status", run_id=run_id)
        assert status["rework_loops"] == {"gate": 1}
        assert status["ready"] == ["build"]

        nxt = await call(c, "graph_next", run_id=run_id)
        assert "needs tests" in nxt["cards"][0]["subagent_prompt"]
        await call(c, "graph_report", run_id=run_id, node_id="build", status="succeeded", summary="added tests")
        nxt = await call(c, "graph_next", run_id=run_id)
        await call(c, "graph_report", run_id=run_id, node_id="review", status="succeeded", summary="lgtm")

        nxt = await call(c, "graph_next", run_id=run_id)
        human = nxt["cards"][0]
        assert human["type"] == "human" and human["task"] == "Ship search?"
        done = await call(c, "graph_report", run_id=run_id, node_id="ok", status="succeeded", summary="approved")
        assert done["status"] == "succeeded"

        runs = await call(c, "graph_runs", workspace=ws)
        assert runs["runs"][0]["run_id"] == run_id
        final = await call(c, "graph_status", workspace=ws)  # latest run
        assert final["status"] == "succeeded" and "flowchart" in final["mermaid"]
    assert (ws_with_graph / ".agents/agflow/runs" / run_id / "report.md").is_file()


async def test_resume_and_abort(ws_with_graph):
    ws = str(ws_with_graph)
    async with Client(build_server()) as c:
        run_id = (await call(c, "graph_start", graph="mini", workspace=ws, inputs={"goal": "g"}))["run_id"]
        await call(c, "graph_next", run_id=run_id)
        resumed = await call(c, "graph_resume", run_id=run_id)
        assert resumed["released"] == ["plan"] and resumed["ready"] == ["plan"]
        aborted = await call(c, "graph_abort", run_id=run_id, reason="changed my mind")
        assert aborted["status"] == "aborted"
        assert (await call(c, "graph_next", run_id=run_id))["cards"] == []


async def test_validate_and_describe(ws_with_graph):
    async with Client(build_server()) as c:
        ok = await call(c, "graph_validate", yaml_text=GRAPH)
        assert ok["ok"] and ok["nodes"] == 6
        bad = await call(c, "graph_validate", yaml_text="id: x\nnodes: []\n")
        assert bad["ok"] is False
        desc = await call(c, "graph_describe", graph="feature-delivery", workspace=str(ws_with_graph))
        assert desc["ok"] and "flowchart" in desc["mermaid"]


async def test_stdio_transport(ws_with_graph, home):
    params = StdioServerParameters(command=sys.executable, args=["-m", "agflow", "mcp", "serve"],
                                   env={"HOME": str(home), "PATH": "/usr/bin:/bin"})
    async with Client(params) as c:
        res = await call(c, "graph_list", workspace=str(ws_with_graph))
        assert "mini" in {g["id"] for g in res["graphs"]}
