"""Improving a workflow from its lessons: the rules, the proposal files, applying, rejecting and undoing.

Nothing here may change a workflow without an explicit apply, and every apply has to be reversible."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agflow import fixes, improve, learn, paths
from agflow.cli import app
from agflow.graph import catalog, engine
from agflow.graph.schema import parse_graph
from tests.conftest import write

NOW = time.time()
DAY = 86400.0
WF = """\
# Our workflow: keep this comment
id: wf
defaults: {retries: 1, timeout_min: 10}
nodes:
  - id: spec            # the spec step
    agent: architect
    outputs: ["docs/spec.md"]
  - id: build
    agent: implementer
    depends_on: [spec]
    acceptance:
      - Tests pass
  - id: review
    agent: reviewer
    depends_on: [build]
  - id: gate
    type: gate
    depends_on: [review]
    on_fail: {goto: build, max_loops: 1}
"""
runner = CliRunner()


@pytest.fixture
def ws(workspace, graphs_dir):
    write(graphs_dir / "wf.yaml", WF)
    return workspace


def graph_of(ws, graph_id="wf"):
    return catalog.resolve(graph_id, ws)[0]


def teach(ws, ledgers, graph_id="wf", *, age_days=1.0):
    """Record one finished run per ledger (a list of ledger events), so the same problem recurs across runs."""
    graph = graph_of(ws, graph_id)
    for i, ledger in enumerate(ledgers):
        run_id = f"{graph_id}-{i}-{len(learn.load_runs(ws, graph_id))}"
        state = engine.new_run(graph, {}, str(ws), now=NOW, run_id=run_id)
        state.status = "succeeded"  # type: ignore[assignment]
        learn.record_run(ws, state, ledger, now=NOW - age_days * DAY)


def reported(node, status, summary):
    return {"event": "reported", "node": node, "status": status, "summary": summary}


def target(ws, graph_id="wf"):
    return paths.workspace_layout(ws).graphs_dir / f"{graph_id}.yaml"


# --- the rules ------------------------------------------------------------------------------------


def test_one_run_is_not_enough_and_nothing_recorded_proposes_nothing(ws):
    assert improve.propose(ws, "wf", now=NOW) is None
    teach(ws, [[reported("build", "failed", "tests did not compile")]])
    assert improve.propose(ws, "wf", now=NOW) is None


def test_repeated_timeouts_raise_the_limit_by_half_at_least_ten_minutes(ws):
    slow = {"event": "retry", "node": "spec"}
    graph = graph_of(ws)
    state = engine.new_run(graph, {}, str(ws), now=1000.0)
    engine.claim(graph, state, now=1000.0)
    engine.recover_stale(graph, state, now=1000.0 + 11 * 60)
    for i in range(2):
        s2 = state.model_copy(deep=True)
        s2.run_id, s2.status = f"wf-t{i}", "succeeded"
        learn.record_run(ws, s2, [slow], now=NOW)
    [change] = improve.propose(ws, "wf", now=NOW).changes
    assert (change.node, change.field, change.old, change.new, change.runs) == ("spec", "timeout_min", 10, 20, 2)
    assert "ran out of time in 2 runs" in change.reason


@pytest.mark.parametrize("current, expected", [(10, 20), (30, 45), (100, 150), (1000, 1440), (1440, None)])
def test_the_new_timeout_is_bounded_by_a_day(current, expected):
    graph = parse_graph({"id": "g", "defaults": {"timeout_min": current}, "nodes": [{"id": "a", "agent": "x"}]})
    lessons = [learn.Lesson("timeout", "a", "no report (lease expired)", f"r{i}", NOW) for i in range(2)]
    got = improve.suggest(graph, lessons, now=NOW)
    assert [c.new for c in got] == ([expected] if expected else [])


def test_repeated_failures_earn_one_more_retry_up_to_three(ws):
    teach(ws, [[reported("build", "failed", "tests did not compile")]] * 2)
    [change] = improve.propose(ws, "wf", now=NOW).changes
    assert (change.node, change.field, change.old, change.new) == ("build", "retries", 1, 2)
    graph = parse_graph({"id": "g", "defaults": {"retries": 3}, "nodes": [{"id": "a", "agent": "x"}]})
    lessons = [learn.Lesson("failed_report", "a", "it failed", f"r{i}", NOW) for i in range(2)]
    assert improve.suggest(graph, lessons, now=NOW) == []


def test_missing_output_that_the_advisory_check_let_through_makes_the_check_required(ws):
    note = {"event": "evidence_warning", "node": "spec", "notes": ["expected output 'docs/spec.md' does not exist"]}
    teach(ws, [[note]] * 2)
    [change] = improve.propose(ws, "wf", now=NOW).changes
    assert (change.node, change.field, change.old, change.new) == ("spec", "evidence", "advisory", "required")
    already = parse_graph({"id": "g", "defaults": {"evidence": "required"},
                           "nodes": [{"id": "a", "agent": "x", "outputs": ["f.md"]}]})
    lessons = [learn.Lesson("evidence_warning", "a", "expected output 'f.md' does not exist", f"r{i}", NOW)
               for i in range(2)]
    assert improve.suggest(already, lessons, now=NOW) == []
    no_outputs = parse_graph({"id": "g", "nodes": [{"id": "a", "agent": "x"}]})
    assert improve.suggest(no_outputs, lessons, now=NOW) == []


def test_a_finding_reviewers_keep_raising_becomes_an_acceptance_criterion_of_the_step_that_gets_reworked(ws):
    teach(ws, [[reported("review", "rejected", "no regression test for the fix")]] * 2)
    [change] = improve.propose(ws, "wf", now=NOW).changes
    assert (change.node, change.field) == ("build", "acceptance")
    assert change.new == "Addresses a finding reviewers raised in 2 earlier runs: no regression test for the fix"
    assert change.reason.startswith("review rejected the same finding in 2 runs")


def test_a_finding_that_is_already_an_acceptance_criterion_is_not_proposed_again(ws):
    teach(ws, [[reported("review", "rejected", "no regression test for the fix")]] * 2)
    proposal = improve.propose(ws, "wf", now=NOW)
    improve.apply(ws, proposal.id)
    assert improve.propose(ws, "wf", now=NOW) is None


def test_old_lessons_do_not_justify_a_change(ws):
    teach(ws, [[reported("build", "failed", "tests did not compile")]] * 2, age_days=learn.WINDOW_DAYS + 5)
    assert improve.propose(ws, "wf", now=NOW) is None


def test_at_most_a_handful_of_changes_per_proposal():
    nodes = [{"id": f"n{i}", "agent": "x"} for i in range(10)]
    graph = parse_graph({"id": "g", "nodes": nodes})
    lessons = [learn.Lesson("failed_report", f"n{i}", "it failed", f"r{j}", NOW) for i in range(10) for j in range(2)]
    assert len(improve.suggest(graph, lessons, now=NOW)) == improve.MAX_CHANGES


def test_lessons_about_a_step_that_no_longer_exists_or_a_gate_are_ignored(ws):
    graph = graph_of(ws)
    lessons = [learn.Lesson("timeout", "ghost", "lease expired", f"r{i}", NOW) for i in range(2)]
    lessons += [learn.Lesson("failed_report", "gate", "it failed", f"r{i}", NOW) for i in range(2)]
    assert improve.suggest(graph, lessons, now=NOW) == []


# --- proposals ------------------------------------------------------------------------------------


def make_proposal(ws):
    teach(ws, [[reported("build", "failed", "tests did not compile"),
                reported("review", "rejected", "no regression test for the fix")]] * 2)
    return improve.propose(ws, "wf", now=NOW)


def test_a_proposal_carries_the_evidence_and_a_real_diff_and_changes_no_workflow(ws):
    before = target(ws).read_text(encoding="utf-8")
    proposal = make_proposal(ws)
    assert [c.field for c in proposal.changes] == ["retries", "acceptance"] and proposal.status == "pending"
    assert "+    retries: 2" in proposal.diff and "# Our workflow: keep this comment" in proposal.diff + before
    removed = [ln for ln in proposal.diff.splitlines() if ln.startswith("-") and not ln.startswith("---")]
    assert removed == [], "the diff only adds: nothing of the workflow is taken away"
    assert target(ws).read_text(encoding="utf-8") == before, "proposing writes only the proposal file"
    assert (paths.workflow_state_dir(ws, "wf") / "proposals" / f"{proposal.id}.json").is_file()


def test_the_same_changes_are_one_proposal(ws):
    first = make_proposal(ws)
    again = improve.propose(ws, "wf", now=NOW + 10)
    assert again.id == first.id and len(improve.proposals(ws, "wf")) == 1


def test_proposal_files_round_trip_and_damaged_ones_are_skipped(ws):
    proposal = make_proposal(ws)
    folder = paths.workflow_state_dir(ws, "wf") / "proposals"
    (folder / "broken.json").write_text("{nope", encoding="utf-8")
    (folder / "odd.json").write_text(json.dumps({"id": "x"}), encoding="utf-8")
    odd_status = json.dumps({**proposal.to_json(), "id": "y", "status": "weird"})
    (folder / "status.json").write_text(odd_status, encoding="utf-8")
    assert [p.id for p in improve.proposals(ws)] == [proposal.id]
    assert improve.get(ws, proposal.id).changes[0].describe() == proposal.changes[0].describe()
    with pytest.raises(improve.ImproveError, match="no proposal 'nope'"):
        improve.get(ws, "nope")
    assert improve.Proposal.from_json(None) is None and improve.Change.from_json({"id": 1}) is None


def test_an_unknown_workflow_or_a_bad_id_is_an_error(ws):
    with pytest.raises(improve.ImproveError, match="unknown graph"):
        improve.propose(ws, "no-such-workflow")
    with pytest.raises(improve.ImproveError, match="not a workflow id"):
        improve.proposals(ws, "../x")


# --- applying -------------------------------------------------------------------------------------


def test_applying_edits_the_yaml_keeps_its_comments_and_leaves_a_valid_workflow(ws):
    proposal = make_proposal(ws)
    done = improve.apply(ws, proposal.id, now=NOW)
    text = target(ws).read_text(encoding="utf-8")
    assert done.status == "applied" and "# Our workflow: keep this comment" in text and "# the spec step" in text
    graph = graph_of(ws)
    assert graph.node("build").retries == 2 and len(graph.node("build").acceptance) == 2
    assert fixes.history()[0]["id"] == done.txn_id and improve.current_status(ws, done) == "applied"


def test_a_bundled_workflow_is_never_edited_a_workspace_copy_is_written(workspace):
    ledgers = [[reported("fix", "failed", "root cause not found")]] * 2
    teach(workspace, ledgers, "bugfix")
    proposal = improve.propose(workspace, "bugfix", now=NOW)
    assert proposal.target != proposal.source
    assert Path(proposal.target).parent == paths.workspace_layout(workspace).graphs_dir
    bundled_before = (paths.bundled_plugin_dir() / "graphs" / "bugfix.yaml").read_bytes()
    assert not target(workspace, "bugfix").exists()
    improve.apply(workspace, proposal.id)
    assert target(workspace, "bugfix").is_file()
    assert (paths.bundled_plugin_dir() / "graphs" / "bugfix.yaml").read_bytes() == bundled_before
    assert catalog.resolve("bugfix", workspace)[0].node("fix").retries == 2
    fixes.undo_last()   # the universal undo also removes the copy that did not exist before
    assert not target(workspace, "bugfix").exists()


def test_only_the_chosen_changes_are_applied(ws):
    proposal = make_proposal(ws)
    done = improve.apply(ws, proposal.id, only=["c2"])
    graph = graph_of(ws)
    assert done.applied_changes == ["c2"] and graph.node("build").retries is None
    assert len(graph.node("build").acceptance) == 2
    with pytest.raises(improve.ImproveError, match="none of"):
        improve.apply(ws, make_proposal(ws).id, only=["c9"])


def test_a_proposal_is_applied_once_and_not_after_the_workflow_changed(ws):
    proposal = make_proposal(ws)
    target(ws).write_text(WF.replace("retries: 1", "retries: 2"), encoding="utf-8")
    with pytest.raises(improve.ImproveError, match="changed since this was proposed"):
        improve.apply(ws, proposal.id)
    target(ws).write_text(WF, encoding="utf-8")
    improve.apply(ws, proposal.id)
    with pytest.raises(improve.ImproveError, match="is applied, not pending"):
        improve.apply(ws, proposal.id)


def test_a_change_that_would_make_the_workflow_invalid_writes_nothing(ws):
    proposal = make_proposal(ws)
    stored = paths.workflow_state_dir(ws, "wf") / "proposals" / f"{proposal.id}.json"
    data = json.loads(stored.read_text(encoding="utf-8"))
    data["changes"][0].update(field="timeout_min", new=0)   # below the schema's minimum
    stored.write_text(json.dumps(data), encoding="utf-8")
    before = target(ws).read_text(encoding="utf-8")
    with pytest.raises(improve.ImproveError, match="would not be valid"):
        improve.apply(ws, proposal.id)
    assert target(ws).read_text(encoding="utf-8") == before and fixes.history() == []


def test_a_proposal_that_would_make_the_workflow_invalid_is_never_stored(ws, monkeypatch):
    """Defence in depth: the rules only suggest values the schema accepts, but if one ever did not, nothing is kept."""
    wrong = improve.Change("c1", "build", "timeout_min", 10, 0, "a rule that went wrong", 2)
    monkeypatch.setattr(improve, "suggest", lambda *args, **kwargs: [wrong])
    with pytest.raises(improve.ImproveError, match="would not be valid"):
        improve.propose(ws, "wf", now=NOW)
    assert improve.proposals(ws, "wf") == []
    assert target(ws).read_text(encoding="utf-8") == WF


def test_a_step_that_vanished_since_the_proposal_is_an_error(ws):
    proposal = make_proposal(ws)
    text = improve.edit(WF, proposal.changes)
    with pytest.raises(improve.ImproveError, match="no step 'ghost'"):
        improve.edit(text, [improve.Change("c1", "ghost", "retries", 1, 2, "r", 2)])


# --- rejecting and undoing ------------------------------------------------------------------------


def test_a_rejected_change_is_not_proposed_again_for_a_month(ws):
    proposal = make_proposal(ws)
    done = improve.reject(ws, proposal.id, "we want it as it is " + "ghp_" + "a" * 30)
    assert done.status == "rejected" and "ghp_" not in done.note
    with pytest.raises(improve.ImproveError, match="is rejected, not pending"):
        improve.reject(ws, proposal.id)
    with pytest.raises(improve.ImproveError, match="is rejected, not pending"):
        improve.apply(ws, proposal.id)
    assert improve.propose(ws, "wf", now=NOW + 10 * DAY) is None
    again = improve.propose(ws, "wf", now=NOW + 31 * DAY)
    assert again is not None and again.status == "pending"


def test_undo_restores_the_file_and_the_fix_history(ws):
    proposal = make_proposal(ws)
    before = target(ws).read_text(encoding="utf-8")
    improve.apply(ws, proposal.id)
    assert fixes.history()
    undone = improve.undo(ws, "wf")
    assert undone.status == "undone" and target(ws).read_text(encoding="utf-8") == before
    assert fixes.history() == [], "the batch is gone from the history: it has been reverted"
    with pytest.raises(improve.ImproveError, match="nothing applied"):
        improve.undo(ws, "wf")


def test_undo_will_not_discard_edits_made_afterwards_unless_forced(ws):
    proposal = make_proposal(ws)
    before = target(ws).read_text(encoding="utf-8")
    improve.apply(ws, proposal.id)
    target(ws).write_text(target(ws).read_text(encoding="utf-8") + "\n# my own edit\n", encoding="utf-8")
    assert improve.current_status(ws, improve.get(ws, proposal.id)) == "applied (file changed since)"
    with pytest.raises(improve.ImproveError, match="edited after the change was applied"):
        improve.undo(ws, "wf")
    improve.undo(ws, "wf", force=True)
    assert target(ws).read_text(encoding="utf-8") == before


def test_status_when_the_file_is_gone(ws):
    proposal = make_proposal(ws)
    done = improve.apply(ws, proposal.id)
    target(ws).unlink()
    assert improve.current_status(ws, done) == "applied (file removed since)"


# --- the command line -----------------------------------------------------------------------------


def cli(ws, *args, input=None):
    return runner.invoke(app, ["improve", *args, "-w", str(ws)], input=input, catch_exceptions=False)


def test_the_cli_walks_through_learn_propose_review_apply_and_undo(ws):
    assert "No workflow has a memory yet" in cli(ws, "lessons").output
    teach(ws, [[reported("build", "failed", "tests did not compile")]] * 2)
    shown = cli(ws, "lessons")
    assert "wf: 2 run(s)" in shown.output and "2x failed_report at build: tests did not compile" in shown.output
    assert json.loads(cli(ws, "lessons", "wf", "--json").output)["wf"]["patterns"][0]["runs"] == 2

    assert "unknown graph" in cli(ws, "propose", "nowhere").output
    made = cli(ws, "propose", "wf")
    pid = made.output.split("proposal ")[1].split(" ")[0]
    assert "1 change(s)" in made.output and cli(ws, "list").output.strip().startswith(pid)

    show = cli(ws, "show", pid)
    assert "retries: 1 -> 2" in show.output and "+    retries: 2" in show.output and "workspace copy" not in show.output

    declined = cli(ws, "apply", pid, input="n\n")
    assert declined.exit_code == 0 and "Nothing changed." in declined.output
    assert graph_of(ws).node("build").retries is None
    applied = cli(ws, "apply", pid, "--yes")
    assert applied.exit_code == 0 and "applied" in applied.output and graph_of(ws).node("build").retries == 2
    assert "applied" in cli(ws, "list", "wf").output

    undone = cli(ws, "undo", "wf")
    assert undone.exit_code == 0 and graph_of(ws).node("build").retries is None


def test_the_cli_names_a_workspace_copy_when_the_workflow_is_bundled(workspace):
    teach(workspace, [[reported("fix", "failed", "root cause not found")]] * 2, "bugfix")
    made = cli(workspace, "propose", "bugfix").output
    pid = made.split("proposal ")[1].split(" ")[0]
    out = cli(workspace, "show", pid).output
    assert "applying writes a workspace copy" in out and "will not reach this workspace" in out


def test_propose_all_visits_every_workflow_with_a_memory(ws):
    teach(ws, [[reported("build", "failed", "tests did not compile")]] * 2)
    out = cli(ws, "propose", "--all").output
    assert "wf: proposal" in out
    assert cli(ws, "propose").exit_code == 1


def test_the_cli_reports_errors_plainly(ws):
    assert cli(ws, "show", "nope").exit_code == 1 and "no proposal 'nope'" in cli(ws, "show", "nope").output
    assert cli(ws, "apply", "nope", "--yes").exit_code == 1
    assert cli(ws, "reject", "nope").exit_code == 1
    assert cli(ws, "undo", "wf").exit_code == 1 and "nothing applied" in cli(ws, "undo", "wf").output
    assert cli(ws, "list", "../x").exit_code == 1


def test_reject_through_the_cli(ws):
    teach(ws, [[reported("build", "failed", "tests did not compile")]] * 2)
    pid = improve.propose(ws, "wf", now=NOW).id
    out = cli(ws, "reject", pid, "--reason", "not now")
    assert out.exit_code == 0 and "30 days" in out.output
    assert improve.get(ws, pid).status == "rejected" and "rejected" in cli(ws, "list").output
