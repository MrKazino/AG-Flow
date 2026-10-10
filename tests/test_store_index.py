"""The run-status index (F3): hook-path lookups must not read every run, and must never be wrong."""

from __future__ import annotations

import functools
import json
import threading
from pathlib import Path

import pytest

from agflow import hooks
from agflow.graph import engine
from agflow.graph.catalog import resolve
from agflow.graph.engine import RunState
from agflow.graph.schema import coerce_inputs
from agflow.graph.store import INDEX_FILE, RunStore
from agflow.validate import validate_inventory
from tests.conftest import write


@pytest.fixture
def store(workspace: Path) -> RunStore:
    return RunStore(workspace)


@functools.lru_cache(maxsize=1)
def _bugfix_graph():
    graph, _ = resolve("bugfix", None)
    return graph


def make_run(store: RunStore, status: str, n: int = 0, *, created_at: float | None = None) -> RunState:
    """A persisted run in the given status. Written through the store, like every real writer."""
    graph = _bugfix_graph()
    state = engine.new_run(graph, coerce_inputs(graph, {"bug": f"b{n}"}), str(store.workspace),
                           now=created_at if created_at is not None else 1_000.0 + n)
    state.status = status  # type: ignore[assignment]
    store._write(state)
    return state


def count_validations(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls = [0]
    real = RunState.model_validate.__func__  # type: ignore[attr-defined]

    def counting(cls, *a, **k):
        calls[0] += 1
        return real(cls, *a, **k)

    monkeypatch.setattr(RunState, "model_validate", classmethod(counting))
    return calls


def ids(runs: list[RunState]) -> list[str]:
    return [r.run_id for r in runs]


# --- correctness: the index never changes the answer ----------------------------------------------------------------


def test_active_matches_a_full_scan(store: RunStore):
    for n, status in enumerate(["running", "succeeded", "failed", "running", "aborted", "running", "succeeded"]):
        make_run(store, status, n)
    expected = [r.run_id for r in store.list() if r.status == "running"]
    assert expected and ids(store.active()) == expected  # same runs, same newest-first order


def test_a_corrupt_state_is_skipped_like_before_and_reported_by_health(store: RunStore):
    good, bad = make_run(store, "running", 1), make_run(store, "running", 2)
    (store.run_dir(bad.run_id) / "state.json").write_text("{broken", encoding="utf-8")
    assert ids(store.active()) == [good.run_id]
    assert ids(store.list()) == [good.run_id]
    assert store.health()["corrupt_runs"] == [bad.run_id]


def test_a_repaired_state_is_picked_up_again(store: RunStore):
    run = make_run(store, "running", 1)
    state_file = store.run_dir(run.run_id) / "state.json"
    good = state_file.read_text(encoding="utf-8")
    state_file.write_text("{broken", encoding="utf-8")
    assert store.active() == []
    state_file.write_text(good + " ", encoding="utf-8")  # different size -> the entry is re-checked
    assert ids(store.active()) == [run.run_id]
    assert store.health()["corrupt_runs"] == []


def test_run_started_by_an_older_agflow_without_an_index_entry_is_found(store: RunStore, workspace: Path):
    older = make_run(store, "running", 1)
    store._index_path().unlink()  # what 0.2.0 left behind: runs but no index
    assert ids(store.active()) == [older.run_id]
    assert older.run_id in json.loads(store._index_path().read_text(encoding="utf-8"))["runs"]


def test_vanished_run_directories_leave_the_index(store: RunStore):
    gone, kept = make_run(store, "running", 1), make_run(store, "running", 2)
    store.active()
    import shutil

    shutil.rmtree(store.run_dir(gone.run_id))
    assert ids(store.active()) == [kept.run_id]
    assert gone.run_id not in json.loads(store._index_path().read_text(encoding="utf-8"))["runs"]


def test_index_saying_running_for_a_finished_run_is_corrected(store: RunStore):
    run = make_run(store, "running", 1)
    state = store.load(run.run_id)
    state.status = "succeeded"
    # a writer that did not maintain the index (older version / crash between the two writes)
    from agflow.fsutil import write_json

    write_json(store.run_dir(run.run_id) / "state.json", state.model_dump(mode="json"))
    assert store.active() == []
    index = json.loads(store._index_path().read_text(encoding="utf-8"))["runs"]
    assert index[run.run_id]["status"] == "succeeded"


def test_crash_between_state_write_and_index_write_loses_nothing(store: RunStore, monkeypatch: pytest.MonkeyPatch):
    graph, _ = resolve("bugfix", store.workspace)
    state = engine.new_run(graph, coerce_inputs(graph, {"bug": "x"}), str(store.workspace), now=5.0)

    def crash(*a, **k):
        raise OSError("simulated crash after the state file was written")

    with monkeypatch.context() as mp:  # scoped: never monkeypatch.undo() (it would also undo the HOME sandbox)
        mp.setattr(store, "_update_index", crash)
        with pytest.raises(OSError, match="simulated crash"):
            store._write(state)
    assert ids(store.active()) == [state.run_id]  # the durable state wins; the index heals


# --- the point of F3: cost does not grow with the number of runs ----------------------------------------------------


def test_active_validates_only_the_running_runs(store: RunStore, monkeypatch: pytest.MonkeyPatch):
    for n in range(60):
        make_run(store, "succeeded", n)
    running = [make_run(store, "running", 100 + n) for n in range(2)]
    store.active()  # warm: the first call after an upgrade indexes what it has not seen
    calls = count_validations(monkeypatch)
    assert ids(store.active()) == [r.run_id for r in reversed(running)]
    assert calls[0] == 2, "62 runs on disk, 2 running: only those 2 may be parsed"


def test_post_tool_use_cost_is_independent_of_total_runs(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    store = RunStore(workspace)
    make_run(store, "running", 0)
    payload = {"cwd": str(workspace), "tool_name": "run_command", "tool_input": {"CommandLine": "ls"}}
    hooks.post_tool_use(payload)  # warm
    with monkeypatch.context() as mp:
        calls = count_validations(mp)
        hooks.post_tool_use(payload)
        few = calls[0]
    for n in range(1, 120):
        make_run(store, "succeeded", n)
    hooks.post_tool_use(payload)  # index the new ones once
    with monkeypatch.context() as mp:
        calls = count_validations(mp)
        hooks.post_tool_use(payload)
    assert calls[0] == few == 1


def test_list_with_a_limit_parses_only_that_many(store: RunStore, monkeypatch: pytest.MonkeyPatch):
    for n in range(40):
        make_run(store, "succeeded", n)
    full_top3 = ids(store.list())[:3]
    store.active()  # warm the index
    calls = count_validations(monkeypatch)
    assert ids(store.list(limit=3)) == full_top3
    assert calls[0] == 3


def test_list_with_a_limit_skips_corrupt_runs_and_still_fills_the_page(store: RunStore):
    runs = [make_run(store, "succeeded", n) for n in range(6)]
    newest = runs[-1]
    (store.run_dir(newest.run_id) / "state.json").write_text("nope", encoding="utf-8")
    page = store.list(limit=3)
    assert ids(page) == [r.run_id for r in reversed(runs[:-1])][:3]


# --- the index is kept current by every writer ------------------------------------------------------------------------


def test_creating_and_finishing_a_run_updates_the_index(store: RunStore, workspace: Path):
    graph, path = resolve("bugfix", workspace)
    state = store.create(graph, coerce_inputs(graph, {"bug": "x"}), graph_path=str(path))
    runs = json.loads(store._index_path().read_text(encoding="utf-8"))["runs"]
    assert runs[state.run_id]["status"] == "running" and runs[state.run_id]["graph_id"] == "bugfix"
    with store.transaction(state.run_id) as s:
        engine.abort(s, "done", now=2_000.0)
    assert json.loads(store._index_path().read_text(encoding="utf-8"))["runs"][state.run_id]["status"] == "aborted"
    assert store.active() == []


def test_concurrent_writers_do_not_lose_index_entries(store: RunStore):
    graph, _ = resolve("bugfix", store.workspace)
    states = [engine.new_run(graph, coerce_inputs(graph, {"bug": str(n)}), str(store.workspace), now=float(n))
              for n in range(24)]
    errors: list[BaseException] = []

    def go(chunk: list[RunState]) -> None:
        try:
            for s in chunk:
                store._write(s)
        except BaseException as exc:  # surfaced through the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=go, args=(states[i::6],)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    indexed = json.loads(store._index_path().read_text(encoding="utf-8"))["runs"]
    assert set(indexed) == {s.run_id for s in states}


# --- damaged index ----------------------------------------------------------------------------------


@pytest.mark.parametrize("damage", ["{not json", "[]", json.dumps({"version": 1, "runs": []}),
                                    json.dumps({"version": 1, "runs": {"x": "not an object"}})])
def test_a_damaged_index_is_set_aside_and_rebuilt(store: RunStore, damage: str):
    run = make_run(store, "running", 1)
    store._index_path().write_text(damage, encoding="utf-8")
    assert ids(store.active()) == [run.run_id]
    assert store._index_path().with_name(INDEX_FILE + ".corrupt").is_file()
    assert store.health()["index_damaged"] is True
    assert run.run_id in json.loads(store._index_path().read_text(encoding="utf-8"))["runs"]


# --- doctor ---------------------------------------------------------------------------------


def test_doctor_reports_unreadable_runs_and_a_damaged_index(workspace: Path):
    from agflow.inventory import Inventory

    store = RunStore(workspace)
    bad = make_run(store, "running", 1)
    (store.run_dir(bad.run_id) / "state.json").write_text("{broken", encoding="utf-8")
    store._index_path().write_text("{broken", encoding="utf-8")
    store.active()
    write(workspace / ".agents" / "rules" / "r.md", "---\ntrigger: manual\n---\nbody\n")
    issues = validate_inventory(Inventory.discover(workspace, include_global=False, include_legacy=False))
    codes = {i.code for i in issues}
    assert {"runs.corrupt", "runs.index.corrupt"} <= codes
    corrupt = next(i for i in issues if i.code == "runs.corrupt")
    assert bad.run_id in corrupt.message and corrupt.level.value == "warning"


def test_doctor_is_quiet_when_runs_are_healthy(workspace: Path):
    from agflow.inventory import Inventory

    make_run(RunStore(workspace), "succeeded", 1)
    issues = validate_inventory(Inventory.discover(workspace, include_global=False, include_legacy=False))
    assert not [i for i in issues if i.code.startswith("runs.")]
