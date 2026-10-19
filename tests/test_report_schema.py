"""`scripts/report_schema.py` and the committed key structure of ``report.json`` (F33).

The nightly SDK job compares the report of a real run with this snapshot. Here the same snapshot is compared with the
report of a run by the fake backend, so it cannot silently fall out of date when the report format changes.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json

import pytest

from agflow.graph.catalog import resolve
from agflow.graph.schema import coerce_inputs
from agflow.graph.store import RunStore
from agflow.runner.backends import FakeBackend
from agflow.runner.runner import run_graph
from tests.conftest import REPO

SNAPSHOT = REPO / "tests" / "snapshots" / "run_report_keys.json"
SMOKE_GRAPH = REPO / "tests" / "fixtures" / "sdk_smoke_graph.yaml"  # what the nightly SDK job runs for real
spec = importlib.util.spec_from_file_location("report_schema", REPO / "scripts" / "report_schema.py")
report_schema = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report_schema)


def fake_run_report(workspace, graph_id=str(SMOKE_GRAPH), inputs=None):
    graph, path = resolve(graph_id, workspace)
    store = RunStore(workspace)
    state = store.create(graph, coerce_inputs(graph, inputs or {}), graph_path=str(path))
    asyncio.run(run_graph(store, state.run_id, FakeBackend()))
    return json.loads((store.run_dir(state.run_id) / "report.json").read_text(encoding="utf-8"))


def test_the_committed_key_structure_matches_what_a_run_writes_today(workspace):
    report = fake_run_report(workspace)
    diff = report_schema.compare(report, json.loads(SNAPSHOT.read_text(encoding="utf-8")))
    assert diff == "", "report.json changed shape; if intended, regenerate the snapshot (see the nightly job):\n" + diff


def test_other_graphs_report_the_same_top_level_keys_and_at_least_the_node_keys(workspace):
    """Gates and human nodes carry other fields than an agent node; the smoke graph has one agent node only."""
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    for graph_id, inputs in (("bugfix", {"bug": "x"}), ("feature-delivery", {"feature": "x", "has_ui": True}),
                             ("security-sweep", {}), ("release", {"version": "1.0.0"})):
        shape = report_schema.key_schema(fake_run_report(workspace, graph_id, inputs))
        assert set(shape) == set(expected), graph_id
        assert set(expected["nodes"][0]) <= set(shape["nodes"][0]), graph_id


@pytest.mark.parametrize(("mutate", "expect"), [
    (lambda r: r.pop("next_action"), '-  "next_action"'),                      # a key disappeared
    (lambda r: r.update(new_key=1), '+  "new_key"'),                           # a key appeared
    (lambda r: [n.pop("attempts") for n in r["nodes"]], '-      "attempts"'),  # inside the node list
    (lambda r: r["counts"].pop("blocked"), '-    "blocked"'),                  # inside a nested map
])
def test_a_changed_report_shape_is_reported_as_a_diff(workspace, mutate, expect):
    report = fake_run_report(workspace)
    mutate(report)
    diff = report_schema.compare(report, json.loads(SNAPSHOT.read_text(encoding="utf-8")))
    assert expect in diff and diff.startswith("--- expected (snapshot)")


def test_values_and_user_keys_do_not_matter(workspace):
    report = fake_run_report(workspace)
    report["summary_text"] = None
    del report["summary_text"]
    report["inputs"] = {"anything": "else", "at": "all"}
    report["rework_loops"] = {"some-gate": 3}
    report["status"] = "failed"
    report["nodes"][0]["summary"] = "a different sentence"
    assert report_schema.compare(report, json.loads(SNAPSHOT.read_text(encoding="utf-8"))) == ""


def test_the_command_line_compares_and_updates(workspace, tmp_path, capsys):
    report_file = tmp_path / "report.json"
    report_file.write_text(json.dumps(fake_run_report(workspace)), encoding="utf-8")
    assert report_schema.main([str(report_file), "--snapshot", str(SNAPSHOT)]) == 0
    broken = json.loads(report_file.read_text(encoding="utf-8"))
    del broken["graph_id"]
    report_file.write_text(json.dumps(broken), encoding="utf-8")
    assert report_schema.main([str(report_file), "--snapshot", str(SNAPSHOT)]) == 1
    assert "no longer has the keys agflow expects" in capsys.readouterr().err
    target = tmp_path / "snap.json"
    assert report_schema.main([str(report_file), "--snapshot", str(target), "--update"]) == 0
    assert report_schema.main([str(report_file), "--snapshot", str(target)]) == 0
