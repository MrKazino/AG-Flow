"""Per-workflow memory: what a finished run teaches, what is kept, and what reaches the next task.

Lessons come from a run's own record by fixed rules; the tests build that record with the real engine and ledger."""

from __future__ import annotations

import asyncio
import json
import time

from mcp import Client

from agflow import learn, paths
from agflow.graph import engine, verify
from agflow.graph.schema import Graph, parse_graph
from agflow.graph.store import RunStore
from agflow.mcp_server import build_server
from agflow.runner.backends import FakeBackend
from agflow.runner.runner import run_graph
from tests.conftest import write

DAY = 86400.0
GRAPH = {"id": "wf", "defaults": {"retries": 1, "evidence": "required", "timeout_min": 10}, "nodes": [
    {"id": "spec", "agent": "architect", "outputs": ["docs/spec.md"]},
    {"id": "build", "agent": "implementer", "depends_on": ["spec"]},
    {"id": "review", "agent": "reviewer", "depends_on": ["build"]},
    {"id": "gate", "type": "gate", "depends_on": ["review"], "on_fail": {"goto": "build", "max_loops": 1}},
]}
SUMMARY = "Wrote the spec and checked it against the template: all sections present and reviewed."


def graph() -> Graph:
    return Graph.model_validate(GRAPH)


def finished_state(status="succeeded", run_id="wf-1") -> engine.RunState:
    g = graph()
    state = engine.new_run(g, {}, "/ws", now=1000.0, run_id=run_id)
    state.status = status  # type: ignore[assignment]
    return state


# --- extraction -----------------------------------------------------------------------------------


def test_each_kind_of_event_becomes_one_lesson():
    ledger = [
        {"event": "evidence_rejected", "node": "spec", "problems": ["expected output 'docs/spec.md' does not exist"]},
        {"event": "evidence_warning", "node": "spec", "notes": ["docs/spec.md was not modified during this attempt"]},
        {"event": "reported", "node": "review", "status": "rejected", "summary": "no regression test for the fix"},
        {"event": "reported", "node": "build", "status": "failed", "summary": "backend error: Boom: network"},
        {"event": "reported", "node": "build", "status": "failed", "summary": "tests did not compile"},
        {"event": "rework", "gate": "gate", "goto": "build", "loop": 1},
        {"event": "human_decision", "node": "approve", "approved": False, "note": "too risky"},
        {"event": "reported", "node": "spec", "status": "succeeded", "summary": "fine"},   # not a lesson
        {"event": "dispatched", "node": "spec"},                                            # not a lesson
    ]
    got = {(item.kind, item.node): item.detail for item in learn.extract(finished_state(), ledger, at=5.0)}
    assert got == {
        ("evidence_rejected", "spec"): "expected output 'docs/spec.md' does not exist",
        ("evidence_warning", "spec"): "docs/spec.md was not modified during this attempt",
        ("review_rejected", "review"): "no regression test for the fix",
        ("backend_error", "build"): "backend error: Boom: network",
        ("failed_report", "build"): "tests did not compile",
        ("rework", "build"): "gate 'gate' sent it back (loop 1)",
        ("human_rejected", "approve"): "too risky",
    }


def test_a_lease_that_expired_is_a_timeout_lesson():
    g = graph()
    state = engine.new_run(g, {}, "/ws", now=1000.0, run_id="wf-t")
    engine.claim(g, state, now=1000.0)
    engine.recover_stale(g, state, now=1000.0 + 11 * 60)   # timeout_min is 10
    found = learn.extract(state, [], at=1.0)
    assert [(item.kind, item.node) for item in found] == [("timeout", "spec")]
    assert "lease expired" in found[0].detail


def test_a_failed_run_is_recorded_with_the_nodes_that_failed():
    g = parse_graph({"id": "wf", "defaults": {"retries": 0}, "nodes": [{"id": "a", "agent": "x"}]})
    state = engine.new_run(g, {}, "/ws", now=1.0, run_id="wf-f")
    engine.claim(g, state, now=1.0)
    engine.report(g, state, "a", "failed", now=2.0, summary="could not do it")
    assert state.status == "failed"
    found = learn.extract(state, [], at=3.0)
    assert [(item.kind, item.detail) for item in found] == [("run_failed", "failed nodes: a")]


def test_a_clean_run_teaches_nothing():
    assert learn.extract(finished_state(), [{"event": "dispatched", "node": "spec"}], at=1.0) == []


def test_details_are_redacted_collapsed_bounded_and_deduplicated():
    secret = "ghp_" + "a" * 30
    summary = f"leaked  {secret}\n\nin   logs"
    ledger = [{"event": "reported", "node": "review", "status": "rejected", "summary": summary}] * 3
    [lesson] = learn.extract(finished_state(), ledger, at=1.0)
    assert secret not in lesson.detail and "***REDACTED***" in lesson.detail and "  " not in lesson.detail
    long = [{"event": "reported", "node": "review", "status": "rejected", "summary": "x" * 1000}]
    assert len(learn.extract(finished_state(), long, at=1.0)[0].detail) == learn.DETAIL_MAX


def test_one_run_teaches_at_most_a_few_lessons():
    ledger = [{"event": "reported", "node": "review", "status": "rejected", "summary": f"finding number {i}"}
              for i in range(50)]
    assert len(learn.extract(finished_state(), ledger, at=1.0)) == learn.MAX_LESSONS_PER_RUN


def test_lessons_survive_json_and_bad_rows_are_dropped():
    lesson = learn.Lesson("timeout", "spec", "ran out of time", "wf-1", 3.5)
    assert learn.Lesson.from_json(lesson.to_json()) == lesson
    for junk in (None, 3, {}, {"kind": "nonsense", "node": "", "detail": "", "run": "", "at": 1},
                 {"kind": "timeout", "node": 1, "detail": "", "run": "", "at": 1}):
        assert learn.Lesson.from_json(junk) is None


def test_two_wordings_that_differ_only_in_numbers_are_the_same_lesson():
    a = learn.Lesson("timeout", "spec", "no report within 30 min (lease expired)", "r1", 1.0)
    b = learn.Lesson("timeout", "spec", "no report within 45 min (lease expired)", "r2", 2.0)
    assert a.key == b.key


# --- recording ------------------------------------------------------------------------------------


def test_recording_is_idempotent_and_writes_the_run_and_its_lessons(workspace):
    state = finished_state("failed")
    ledger = [{"event": "reported", "node": "review", "status": "rejected", "summary": "needs a test"}]
    assert learn.record_run(workspace, state, ledger, now=10.0) == 2     # review_rejected + run_failed
    assert learn.record_run(workspace, state, ledger, now=11.0) == 0
    [row] = learn.load_runs(workspace, "wf")
    assert row["run"] == "wf-1" and row["status"] == "failed"
    assert set(row["nodes"]) == {"spec", "build", "review", "gate"}
    assert [(item.kind, item.run) for item in learn.load_lessons(workspace, "wf")] == [
        ("review_rejected", "wf-1"), ("run_failed", "wf-1")]
    assert (paths.workflow_state_dir(workspace, "wf") / learn.LESSONS_FILE).is_file()


def test_a_run_that_is_still_going_is_not_recorded(workspace):
    assert learn.record_run(workspace, finished_state("running"), []) == 0
    assert learn.load_runs(workspace, "wf") == []


def test_an_aborted_run_is_counted_but_teaches_nothing(workspace):
    ledger = [{"event": "reported", "node": "review", "status": "rejected", "summary": "needs a test"}]
    assert learn.record_run(workspace, finished_state("aborted"), ledger) == 0
    assert learn.load_runs(workspace, "wf")[0]["status"] == "aborted" and learn.load_lessons(workspace, "wf") == []


def test_a_graph_id_that_is_not_a_folder_name_is_refused(workspace):
    state = finished_state("failed")
    state.graph_id = "../../etc"
    assert learn.record_run(workspace, state, []) == 0 and learn.workflow_dir(workspace, "../x") is None
    assert not (workspace / ".agents" / "agflow" / "workflows").exists()
    assert learn.load_lessons(workspace, "../x") == [] and learn.load_runs(workspace, "") == []


def test_recording_never_raises_when_the_folder_cannot_be_written(workspace):
    blocker = paths.workspace_state_dir(workspace) / "workflows"
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("a file where the folder should be", encoding="utf-8")
    assert learn.record_run(workspace, finished_state("failed"), []) == 0


def test_the_files_are_capped_oldest_first(workspace):
    ledger = [{"event": "reported", "node": "review", "status": "rejected", "summary": "same problem"}]
    for i in range(learn.MAX_RUN_LINES + 5):
        learn.record_run(workspace, finished_state("succeeded", run_id=f"wf-{i:04d}"), ledger, now=float(i))
    runs = learn.load_runs(workspace, "wf")
    assert len(runs) == learn.MAX_RUN_LINES and runs[0]["run"] == "wf-0005" and runs[-1]["run"].endswith("0204")
    assert len(learn.load_lessons(workspace, "wf")) == learn.MAX_RUN_LINES + 5


def test_damaged_lines_are_skipped_on_read(workspace):
    folder = paths.workflow_state_dir(workspace, "wf")
    folder.mkdir(parents=True)
    good = json.dumps(learn.Lesson("timeout", "spec", "slow", "r1", 1.0).to_json())
    (folder / learn.LESSONS_FILE).write_text(f"{{broken\n{good}\n[1]\n\n", encoding="utf-8")
    assert len(learn.load_lessons(workspace, "wf")) == 1


# --- reading --------------------------------------------------------------------------------------


def L(kind, node, detail, run, age_days, now=100 * DAY):
    return learn.Lesson(kind, node, detail, run, now - age_days * DAY)


def test_patterns_count_distinct_runs_and_rank_recent_repeats_first():
    now = 100 * DAY
    lessons = [L("timeout", "spec", "no report within 10 min (lease expired)", "r1", 2),
               L("timeout", "spec", "no report within 20 min (lease expired)", "r2", 3),
               L("timeout", "spec", "no report within 20 min (lease expired)", "r2", 3),   # same run again
               L("review_rejected", "review", "needs a test", "r1", 40),
               L("rework", "build", "gate 'gate' sent it back (loop 1)", "r3", 1)]
    found = learn.patterns(lessons, now=now)
    assert [(p.kind, p.runs) for p in found] == [("timeout", 2), ("rework", 1), ("review_rejected", 1)]
    assert found[0].detail.endswith("(lease expired)") and found[0].score > found[1].score > found[2].score


def test_old_lessons_fall_out_of_the_window_and_the_node_filter_works():
    lessons = [L("timeout", "spec", "slow", "r1", learn.WINDOW_DAYS + 1), L("timeout", "build", "slow", "r2", 1)]
    assert [p.node for p in learn.patterns(lessons, now=100 * DAY)] == ["build"]
    assert learn.patterns(lessons, now=100 * DAY, node="spec") == []


def test_a_task_hears_only_what_has_been_seen_often_enough():
    index = learn.LessonIndex.__new__(learn.LessonIndex)
    index.now = 100 * DAY
    index.lessons = [L("evidence_rejected", "spec", "expected output 'a.md' does not exist", "r1", 1),
                     L("review_rejected", "spec", "no regression test", "r1", 1),
                     L("timeout", "spec", "lease expired", "r1", 1),                   # one run: not yet
                     L("failed_report", "spec", "tests did not compile", "r1", 1),
                     L("failed_report", "spec", "tests did not compile", "r2", 2),
                     L("run_failed", "spec", "failed nodes: spec", "r1", 1),           # never shown to an agent
                     L("evidence_rejected", "other", "unrelated", "r1", 1)]
    lines = index.for_node("spec")
    assert len(lines) == 3 and not any("other" in s or "unrelated" in s for s in lines)
    assert any("evidence check" in s and "a.md" in s for s in lines)
    assert not any("ran out of time" in s for s in lines)
    assert index.for_node("spec", limit=1) != []


def test_what_a_task_hears_is_short():
    index = learn.LessonIndex.__new__(learn.LessonIndex)
    index.now = 100 * DAY
    index.lessons = [L("review_rejected", "spec", "y" * 230 + str(i), f"r{i}", 1) for i in range(10)]
    assert sum(len(s) for s in index.for_node("spec", limit=10)) <= learn.CARD_CHARS + 230


def test_lessons_for_card_never_raises(workspace, monkeypatch):
    assert learn.lessons_for_card(workspace, "wf", ["spec"]) == {"spec": []}

    def boom(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(learn, "LessonIndex", boom)
    assert learn.lessons_for_card(workspace, "wf", ["spec"]) == {}


# --- on the card ----------------------------------------------------------------------------------


def test_lessons_appear_in_the_task_only_when_there_are_some():
    g = graph()
    state = engine.new_run(g, {}, "/ws", now=1.0)
    engine.claim(g, state, now=1.0)
    plain = engine.build_card(g, state, "spec")
    assert plain["lessons"] == [] and "Lessons from earlier runs" not in plain["subagent_prompt"]
    said = "A reviewer rejected earlier work at this step (2x): no test"
    taught = engine.build_card(g, state, "spec", lessons=[said])
    assert f"## Lessons from earlier runs of this workflow\n- {said}" in taught["subagent_prompt"]


# --- end to end -----------------------------------------------------------------------------------


E2E = """
id: e2e
defaults: {retries: 1, evidence: required}
nodes:
  - {id: write, agent: implementer, outputs: ["docs/{{ run.id }}.md"]}
  - {id: gate, type: gate, depends_on: [write]}
"""


def parse(result):
    return result.structured_content or json.loads(result.content[0].text)


async def test_a_finished_mcp_run_is_remembered_and_the_next_run_hears_about_it(workspace, graphs_dir):
    write(graphs_dir / "e2e.yaml", E2E)
    async with Client(build_server()) as c:
        async def call(tool, **args):
            return parse(await c.call_tool(tool, args))

        first = (await call("graph_start", graph="e2e", workspace=str(workspace), inputs={}))["run_id"]
        await call("graph_next", run_id=first)
        report = {"run_id": first, "node_id": "write", "status": "succeeded", "summary": SUMMARY}
        assert (await call("graph_report", **report))["evidence"]["rejected"] is True      # the file was never written
        await call("graph_next", run_id=first)
        write(workspace / "docs" / f"{first}.md", "# done\n")
        done = await call("graph_report", **report)
        assert done["status"] == "succeeded"
        ledger = RunStore(workspace).read_ledger(first)
        assert [e["count"] for e in ledger if e["event"] == "lessons_recorded"] == [1]

        seen = await call("graph_lessons", graph="e2e", workspace=str(workspace))
        assert seen["runs"] == {"recorded": 1, "succeeded": 1, "failed": 0}
        assert [(p["kind"], p["node"], p["runs"]) for p in seen["patterns"]] == [("evidence_rejected", "write", 1)]
        assert (await call("graph_lessons", graph="e2e", workspace=str(workspace), node="nothing"))["patterns"] == []
        assert (await call("graph_lessons", graph="../x", workspace=str(workspace)))["ok"] is False

        second = (await call("graph_start", graph="e2e", workspace=str(workspace), inputs={}))["run_id"]
        card = (await call("graph_next", run_id=second))["cards"][0]
        assert "expected output" in card["subagent_prompt"].split("## Lessons from earlier runs", 1)[1].split("##")[0]
        assert card["lessons"] and "evidence check" in card["lessons"][0]


def test_a_headless_run_remembers_what_went_wrong(workspace):
    from agflow.graph.catalog import resolve
    from agflow.graph.schema import coerce_inputs

    g, path = resolve("security-sweep", workspace)
    store = RunStore(workspace)
    state = store.create(g, coerce_inputs(g, {}), graph_path=str(path))
    final = asyncio.run(run_graph(store, state.run_id, FakeBackend(), approve_human=True))   # claims files, writes none
    assert final.status == "failed"
    kinds = {(item.kind, item.node) for item in learn.load_lessons(workspace, g.id)}
    assert ("evidence_rejected", "code-audit") in kinds and ("run_failed", "") in kinds
    events = [e["event"] for e in store.read_ledger(state.run_id)]
    assert events.index("lessons_recorded") < events.index("runner_stopped")
    assert learn.load_runs(workspace, g.id)[0]["status"] == "failed"


def test_nothing_in_a_dry_run_that_passes_is_remembered_as_a_problem(workspace):
    from agflow.graph.catalog import resolve
    from agflow.graph.schema import coerce_inputs

    g, path = resolve("ui-feature", workspace)
    store = RunStore(workspace)
    state = store.create(g, coerce_inputs(g, {"feature": "navbar"}), graph_path=str(path))
    asyncio.run(run_graph(store, state.run_id, FakeBackend(workspace=workspace), approve_human=True))
    assert learn.load_lessons(workspace, g.id) == [] and learn.load_runs(workspace, g.id)[0]["status"] == "succeeded"


def test_the_verifier_module_and_learning_agree_on_what_a_rejection_looks_like(workspace):
    g = graph()
    state = engine.new_run(g, {}, str(workspace), now=time.time(), run_id="wf-agree")
    engine.claim(g, state, now=time.time())
    ev = verify.check(g, state, "spec", status="succeeded", summary=SUMMARY)
    events = engine.report(g, state, "spec", "succeeded", now=time.time(), summary=SUMMARY,
                           evidence_problems=ev.problems, evidence_notes=ev.warnings)
    [lesson] = learn.extract(state, events, at=1.0)
    assert lesson.kind == "evidence_rejected" and "docs/spec.md" in lesson.detail
