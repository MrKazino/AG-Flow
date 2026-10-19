"""The evidence gate: what an agent claims is checked against the workspace before the engine accepts it.

Covers the schema (modes and declarative checks), ``graph.verify`` (every kind of finding), the engine's handling of a
rejected report, and the two callers (the MCP tool and the headless runner)."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest
from mcp import Client
from pydantic import ValidationError

from agflow.graph import engine, verify
from agflow.graph.catalog import list_graphs, resolve
from agflow.graph.schema import Check, Graph, GraphError, coerce_inputs, parse_graph
from agflow.graph.store import RunStore
from agflow.mcp_server import build_server
from agflow.runner.backends import FakeBackend
from agflow.runner.runner import run_graph
from tests.conftest import write

GOOD = "Wrote the spec to docs/out.md and checked every section against the template; the review passed."


def graph_of(evidence="required", outputs=("docs/out.md",), checks=None, retries=1, node_evidence=None) -> Graph:
    node = {"id": "work", "agent": "implementer", "outputs": list(outputs)}
    if checks:
        node["checks"] = checks
    if node_evidence:
        node["evidence"] = node_evidence
    return Graph.model_validate({"id": "g", "defaults": {"retries": retries, "evidence": evidence},
                                 "nodes": [node, {"id": "after", "type": "gate", "depends_on": ["work"]}]})


def running(graph: Graph, workspace: Path, inputs=None) -> engine.RunState:
    state = engine.new_run(graph, inputs or {}, str(workspace), now=time.time())
    engine.claim(graph, state, now=time.time())
    return state


def submit(graph, state, status="succeeded", summary=GOOD, artifacts=None, node="work"):
    ev = verify.check(graph, state, node, status=status, summary=summary, artifacts=artifacts)
    events = engine.report(graph, state, node, status, now=time.time(), summary=summary, artifacts=artifacts,
                           evidence_problems=ev.problems, evidence_notes=ev.warnings)
    return ev, events


def kinds(events) -> list[str]:
    return [e["event"] for e in events]


@pytest.fixture
def ws(workspace) -> Path:
    return workspace


# --- the schema -----------------------------------------------------------------------------------


def test_evidence_defaults_to_advisory_and_a_node_can_override_it():
    g = graph_of(evidence="advisory", node_evidence="required")
    assert g.defaults.evidence == "advisory" and g.evidence_for(g.node("work")) == "required"
    assert Graph.model_validate({"id": "x", "nodes": [{"id": "a", "agent": "b"}]}).defaults.evidence == "advisory"


@pytest.mark.parametrize("bad", ["strict", "REQUIRED", True, 1])
def test_unknown_evidence_modes_are_rejected(bad):
    with pytest.raises(ValidationError):
        graph_of(evidence=bad)
    with pytest.raises(ValidationError):
        graph_of(node_evidence=bad)


def test_a_check_takes_one_string_or_a_list_and_nothing_unknown():
    assert Check(file="a.md", contains="x").contains == ["x"]
    assert Check(file="a.md", contains=["x", "y"], not_contains="TODO", min_bytes=10).not_contains == ["TODO"]
    with pytest.raises(ValidationError):
        Check(file="a.md", surprise=1)  # type: ignore[call-arg]


@pytest.mark.parametrize("kwargs", [
    {"file": ""}, {"file": "x" * 301}, {"file": "a", "contains": [""]}, {"file": "a", "contains": ["x" * 201]},
    {"file": "a", "contains": [str(i) for i in range(11)]}, {"file": "a", "min_bytes": 0},
    {"file": "a", "min_bytes": 10_000_001},
])
def test_checks_are_bounded(kwargs):
    with pytest.raises(ValidationError):
        Check(**kwargs)


def test_at_most_twenty_checks_per_node_and_only_agent_nodes_have_them():
    many = [{"file": f"f{i}.md"} for i in range(21)]
    with pytest.raises(ValidationError):
        graph_of(checks=many)
    gate = {"id": "g1", "type": "gate", "depends_on": ["work"]}
    for extra in ({"evidence": "required"}, {"checks": [{"file": "a"}]}):
        with pytest.raises(ValidationError, match="only agent nodes"):
            Graph.model_validate({"id": "g", "nodes": [{"id": "work", "agent": "a"}, {**gate, **extra}]})


def test_a_template_error_in_a_check_is_reported_at_load_time():
    with pytest.raises(GraphError, match="template syntax error"):
        parse_graph({"id": "g", "nodes": [{"id": "a", "agent": "b", "checks": [{"file": "{{ unclosed"}]}]})


def test_every_bundled_node_that_declares_outputs_requires_evidence(workspace):
    for row in list_graphs(workspace):
        graph, _ = resolve(row.id, workspace)
        for node in graph.nodes:
            if node.outputs:
                assert graph.evidence_for(node) == "required", f"{graph.id}.{node.id} declares outputs"


# --- what is checked ------------------------------------------------------------------------------


def test_off_checks_nothing_and_only_succeeded_agent_reports_are_checked(ws):
    g = graph_of(evidence="off")
    state = running(g, ws)
    assert verify.check(g, state, "work", status="succeeded", summary="").mode == "off"
    assert not verify.check(g, state, "work", status="succeeded", summary="").problems
    g = graph_of()
    state = running(g, ws)
    for status in ("failed", "rejected"):
        assert verify.check(g, state, "work", status=status, summary="").problems == []
    assert verify.check(g, state, "after", status="succeeded").problems == []   # a gate
    assert verify.check(g, state, "nonexistent", status="succeeded").mode == "off"


def test_a_missing_empty_or_present_output(ws):
    g = graph_of()
    state = running(g, ws)
    assert verify.check(g, state, "work", status="succeeded", summary=GOOD).problems == [
        "expected output 'docs/out.md' does not exist"]
    write(ws / "docs" / "out.md", "")
    assert verify.check(g, state, "work", status="succeeded", summary=GOOD).problems == [
        "expected output 'docs/out.md' is empty"]
    write(ws / "docs" / "out.md", "# Spec\n")
    ok = verify.check(g, state, "work", status="succeeded", summary=GOOD)
    assert ok.problems == [] and ok.warnings == [] and not ok.rejected


def test_a_trailing_slash_means_a_folder_that_has_something_in_it(ws):
    g = graph_of(outputs=("evidence/run1/",))
    state = running(g, ws)
    (ws / "evidence" / "run1").mkdir(parents=True)
    assert "is an empty folder" in verify.check(g, state, "work", status="succeeded", summary=GOOD).problems[0]
    write(ws / "evidence" / "run1" / "shot.png.txt", "x")
    assert verify.check(g, state, "work", status="succeeded", summary=GOOD).problems == []
    g2 = graph_of(outputs=("docs/out.md/",))
    write(ws / "docs" / "out.md", "file, not folder")
    assert "a folder was expected" in verify.check(g2, running(g2, ws), "work", status="succeeded",
                                                   summary=GOOD).problems[0]


def test_glob_outputs_need_one_non_empty_match(ws):
    g = graph_of(outputs=("reports/*.md",))
    state = running(g, ws)
    assert "matches no non-empty file" in verify.check(g, state, "work", status="succeeded", summary=GOOD).problems[0]
    write(ws / "reports" / "a.md", "")
    assert verify.check(g, state, "work", status="succeeded", summary=GOOD).problems
    write(ws / "reports" / "b.md", "content")
    assert verify.check(g, state, "work", status="succeeded", summary=GOOD).problems == []


def test_outputs_are_rendered_with_the_run_and_the_inputs(ws):
    g = graph_of(outputs=("docs/{{ inputs.name }}-{{ run.id }}.md",))
    state = running(g, ws, {"name": "login"})
    wanted = f"docs/login-{state.run_id}.md"
    assert verify.check(g, state, "work", status="succeeded", summary=GOOD).problems == [
        f"expected output {wanted!r} does not exist"]
    write(ws / wanted, "x")
    assert verify.check(g, state, "work", status="succeeded", summary=GOOD).problems == []


def test_outputs_outside_the_workspace_are_a_problem(ws, tmp_path):
    outside = tmp_path / "elsewhere.md"
    outside.write_text("x", encoding="utf-8")
    for path in ("../elsewhere.md", str(outside)):
        g = graph_of(outputs=(path,))
        assert "outside the workspace" in verify.check(g, running(g, ws), "work", status="succeeded",
                                                       summary=GOOD).problems[0]


def test_a_link_inside_the_workspace_that_points_outside_does_not_count(ws, tmp_path, require_symlinks):
    outside = tmp_path / "secret.md"
    outside.write_text("x", encoding="utf-8")
    (ws / "docs").mkdir()
    (ws / "docs" / "out.md").symlink_to(outside)
    g = graph_of()
    assert "outside the workspace" in verify.check(g, running(g, ws), "work", status="succeeded",
                                                   summary=GOOD).problems[0]


def test_reported_artifacts_must_exist(ws):
    g = graph_of(outputs=())
    state = running(g, ws)
    write(ws / "src" / "real.py", "x = 1\n")
    ev = verify.check(g, state, "work", status="succeeded", summary=GOOD,
                      artifacts=["src/real.py", "src/invented.py", "deleted: src/old.py", "https://example.com/x",
                                 "", "  "])
    assert ev.problems == ["reported artifact 'src/invented.py' does not exist"]


def test_an_artifact_outside_the_workspace_is_noted_not_blocked(ws, tmp_path):
    g = graph_of(outputs=())
    ev = verify.check(g, running(g, ws), "work", status="succeeded", summary=GOOD,
                      artifacts=[str(tmp_path / "somewhere.txt")])
    assert ev.problems == [] and "outside the workspace: not checked" in ev.warnings[0]


def test_only_the_first_fifty_artifacts_are_looked_at(ws):
    g = graph_of(outputs=())
    ev = verify.check(g, running(g, ws), "work", status="succeeded", summary=GOOD,
                      artifacts=[f"missing{i}.py" for i in range(80)])
    assert len(ev.problems) == verify.MAX_ARTIFACTS


@pytest.mark.parametrize("summary", ["", "   ", "done", "Done.", "OK!", "all good", "x" * 19])
def test_a_summary_that_says_nothing_checkable_is_a_problem(ws, summary):
    g = graph_of(outputs=())
    ev = verify.check(g, running(g, ws), "work", status="succeeded", summary=summary)
    assert ev.problems and "says nothing checkable" in ev.problems[0]


def test_declarative_checks_on_file_content(ws):
    checks = [{"file": "docs/out.md", "contains": ["## Acceptance", "tests"], "not_contains": ["TODO", "FIXME"],
               "min_bytes": 30}]
    g = graph_of(checks=checks)
    state = running(g, ws)
    write(ws / "docs" / "out.md", "# Spec\n## Acceptance\ntests pass; TODO later\n")
    problems = verify.check(g, state, "work", status="succeeded", summary=GOOD).problems
    assert problems == ["docs/out.md must not contain 'TODO'"]
    write(ws / "docs" / "out.md", "# Spec\n")
    assert verify.check(g, state, "work", status="succeeded", summary=GOOD).problems == [
        "docs/out.md is smaller than 30 bytes", "docs/out.md must contain '## Acceptance'",
        "docs/out.md must contain 'tests'"]
    write(ws / "docs" / "out.md", "# Spec\n## Acceptance\ntests all pass, nothing left to do here\n")
    assert verify.check(g, state, "work", status="succeeded", summary=GOOD).problems == []


def test_checks_can_use_inputs_and_report_missing_or_outside_files(ws):
    checks = [{"file": "docs/{{ inputs.n }}.md", "contains": "{{ inputs.n }}"}, {"file": "../escape.md"}]
    g = graph_of(outputs=(), checks=checks)
    state = running(g, ws, {"n": "login"})
    problems = verify.check(g, state, "work", status="succeeded", summary=GOOD).problems
    assert problems == ["check on 'docs/login.md': the file does not exist",
                        "check on '../escape.md': the path is outside the workspace"]
    write(ws / "docs" / "login.md", "about login")
    assert verify.check(g, state, "work", status="succeeded", summary=GOOD).problems == [
        "check on '../escape.md': the path is outside the workspace"]


def test_only_the_start_of_a_large_file_is_read(ws):
    g = graph_of(outputs=(), checks=[{"file": "big.txt", "contains": "THE-END"}])
    write(ws / "big.txt", "a" * (verify.MAX_READ + 10) + "THE-END")
    assert verify.check(g, running(g, ws), "work", status="succeeded", summary=GOOD).problems == [
        "big.txt must contain 'THE-END'"]


def test_files_the_summary_mentions_but_that_do_not_exist_are_noted(ws):
    g = graph_of(outputs=("docs/out.md",))
    write(ws / "docs" / "out.md", "x")
    write(ws / "src" / "real.py", "x")
    summary = ("Fixed `src/real.py` and `src/ghost.py`, updated `docs/out.md`, bumped to `1.2.3`, see `https://x.io/a.md`"
               " and `src/also_missing.py`.")
    ev = verify.check(g, running(g, ws), "work", status="succeeded", summary=summary)
    assert ev.problems == []
    assert ev.warnings == ["the summary mentions files that do not exist in the workspace: src/ghost.py, "
                           "src/also_missing.py"]


def test_an_output_left_over_from_before_this_attempt_is_noted(ws):
    g = graph_of()
    write(ws / "docs" / "out.md", "from an earlier run")
    old = time.time() - 3600
    os.utime(ws / "docs" / "out.md", (old, old))
    ev = verify.check(g, running(g, ws), "work", status="succeeded", summary=GOOD)
    assert ev.problems == [] and "was not modified during this attempt" in ev.warnings[0]


# --- advisory versus required ---------------------------------------------------------------------


def test_advisory_records_the_findings_as_warnings_and_accepts_the_report(ws):
    g = graph_of(evidence="advisory")
    state = running(g, ws)
    ev, events = submit(g, state, summary="done")
    assert not ev.rejected and ev.problems == [] and len(ev.warnings) == 2
    assert state.nodes["work"].status == engine.Status.SUCCEEDED
    assert kinds(events) == ["succeeded", "evidence_warning", "gate_passed", *kinds(events)[3:]]
    assert state.nodes["work"].evidence == ev.warnings


def test_required_turns_a_failed_check_into_a_retry_with_exact_feedback(ws):
    g = graph_of()
    state = running(g, ws)
    ev, events = submit(g, state, artifacts=["docs/out.md"])
    assert ev.rejected and kinds(events)[:2] == ["evidence_rejected", "retry"]
    node = state.nodes["work"]
    assert node.status == engine.Status.PENDING and node.attempts == 1
    assert "evidence check failed: expected output 'docs/out.md' does not exist" in node.feedback[0]
    assert "reported artifact 'docs/out.md' does not exist" in node.feedback[0]
    card = engine.build_card(g, state, "work")
    assert "Feedback you must address" in card["subagent_prompt"] and "does not exist" in card["subagent_prompt"]


def test_the_retry_can_succeed_once_the_evidence_exists(ws):
    g = graph_of()
    state = running(g, ws)
    submit(g, state)
    engine.claim(g, state, now=time.time())
    write(ws / "docs" / "out.md", "# Spec\n")
    ev, events = submit(g, state)
    node = state.nodes["work"]
    assert not ev.rejected and node.status == engine.Status.SUCCEEDED and node.attempts == 2
    assert state.nodes["work"].feedback == [] and state.nodes["work"].evidence == []


def test_when_no_attempts_are_left_a_rejected_report_fails_the_node_and_the_run(ws):
    g = graph_of(retries=0)
    state = running(g, ws)
    ev, events = submit(g, state)
    node = state.nodes["work"]
    assert node.status == engine.Status.FAILED and node.error.startswith("evidence check failed:")
    assert "failed" in kinds(events) and state.status == "failed"


def test_a_long_list_of_problems_is_bounded(ws):
    g = graph_of(outputs=(), retries=0)
    state = running(g, ws)
    submit(g, state, artifacts=[f"missing/{'x' * 40}{i}.py" for i in range(40)])
    assert len(state.nodes["work"].error) <= 1500


def test_reports_without_evidence_arguments_behave_as_before(ws):
    g = graph_of()
    state = running(g, ws)
    events = engine.report(g, state, "work", "succeeded", now=time.time(), summary="x")
    assert state.nodes["work"].status == engine.Status.SUCCEEDED and "evidence_rejected" not in kinds(events)


def test_the_node_state_with_evidence_survives_a_save_and_load(ws):
    g = graph_of(evidence="advisory")
    state = running(g, ws)
    submit(g, state, summary="done")
    again = engine.RunState.model_validate_json(state.model_dump_json())
    assert again.nodes["work"].evidence == state.nodes["work"].evidence != []
    old_shape = json.loads(state.model_dump_json())
    del old_shape["nodes"]["work"]["evidence"]   # a run file written before this field existed
    assert engine.RunState.model_validate(old_shape).nodes["work"].evidence == []


# --- the callers ----------------------------------------------------------------------------------


GRAPH_YAML = """
id: evidenced
defaults: {retries: 1, evidence: required}
nodes:
  - {id: write, agent: implementer, outputs: ["docs/{{ run.id }}.md"]}
  - {id: gate, type: gate, depends_on: [write]}
"""


def parse(result):
    return result.structured_content or json.loads(result.content[0].text)


async def test_the_mcp_tool_rejects_phantom_work_and_accepts_real_work(workspace, graphs_dir):
    write(graphs_dir / "evidenced.yaml", GRAPH_YAML)
    async with Client(build_server()) as c:
        async def call(tool, **args):
            return parse(await c.call_tool(tool, args))

        started = await call("graph_start", graph="evidenced", workspace=str(workspace), inputs={})
        run_id = started["run_id"]
        await call("graph_next", run_id=run_id)
        claimed_report = {"run_id": run_id, "node_id": "write", "status": "succeeded", "summary": GOOD}
        first = await call("graph_report", **claimed_report)
        assert first["ok"] and first["evidence"]["rejected"] is True
        assert "does not exist" in first["evidence"]["problems"][0]
        assert {"evidence_rejected", "retry"} <= {e["event"] for e in first["events"]}
        assert first["ready"] == ["write"], "the node goes back to ready, with feedback"

        again = await call("graph_next", run_id=run_id)
        assert "Feedback you must address" in again["cards"][0]["subagent_prompt"]
        write(workspace / "docs" / f"{run_id}.md", "# the real thing\n")
        second = await call("graph_report", **claimed_report, artifacts=[f"docs/{run_id}.md"])
        assert second["ok"] and "evidence" not in second and second["status"] == "succeeded"
        ledger = (workspace / ".agents" / "agflow" / "runs" / run_id / "ledger.jsonl").read_text(encoding="utf-8")
        assert '"evidence_rejected"' in ledger


def test_the_headless_runner_applies_the_same_gate_to_a_backend_that_claims_files_it_never_wrote(workspace):
    graph, path = resolve("security-sweep", workspace)
    store = RunStore(workspace)
    state = store.create(graph, coerce_inputs(graph, {}), graph_path=str(path))
    final = asyncio.run(run_graph(store, state.run_id, FakeBackend(), approve_human=True))   # no workspace: no files
    assert final.status == "failed"
    node = final.nodes["code-audit"]
    assert node.status == engine.Status.FAILED and "does not exist" in node.error
    ledger = (store.run_dir(state.run_id) / "ledger.jsonl").read_text(encoding="utf-8")
    assert '"evidence_rejected"' in ledger


@pytest.mark.parametrize("graph_id, inputs", [
    ("security-sweep", {}), ("feature-delivery", {"feature": "x", "has_ui": True}),
    ("ui-feature", {"feature": "navbar"}),
])
def test_a_dry_run_with_a_workspace_writes_placeholders_and_passes_the_gate(workspace, graph_id, inputs):
    graph, path = resolve(graph_id, workspace)
    store = RunStore(workspace)
    state = store.create(graph, coerce_inputs(graph, inputs), graph_path=str(path))
    final = asyncio.run(run_graph(store, state.run_id, FakeBackend(workspace=workspace), approve_human=True))
    assert final.status == "succeeded", {n: s.error for n, s in final.nodes.items() if s.error}
    wrote = [p for p in workspace.rglob("*") if p.is_file() and "agflow" not in p.parts[-3:-1]]
    assert any("fake" in p.read_text(encoding="utf-8") for p in wrote)


def test_the_fake_backend_only_writes_inside_the_workspace(workspace, tmp_path):
    backend = FakeBackend(workspace=workspace)
    card = {"node_id": "n", "title": "T", "expected_outputs": ["../escaped.md", str(tmp_path / "abs.md"), "ok/x.md",
                                                               "glob/*.md"]}
    asyncio.run(backend.run(card))
    assert (workspace / "ok" / "x.md").is_file()
    assert not (workspace.parent / "escaped.md").exists() and not (tmp_path / "abs.md").exists()
    assert not (workspace / "glob").exists()
