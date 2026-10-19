"""H1: a hook must never be the reason an Antigravity session fails, and must cost almost nothing when it has no work.

What Antigravity does with a hook that misbehaves (from the engine itself: "pre-tool hooks failed", "Stop hook
failed", "unrecognized pre-tool hook decision ...; blocked execution for safety") is why each of these is pinned."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agflow import hook_health, hooks, paths
from agflow.graph.store import RunStore
from tests.conftest import write
from tests.test_hooks import _active_run
from tests.test_store_index import make_run


def run_cli(event: str, stdin: bytes, **env: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run([sys.executable, "-m", "agflow", "hook", event], input=stdin, capture_output=True, timeout=60,
                          env={**os.environ, **env}, check=False)


# --- the answer is always a documented shape ---------------------------------------------------------------------


@pytest.mark.parametrize("event, given, expected", [
    ("pre-tool-use", {"decision": "allow"}, {}),  # never "allow": it would bypass the user's own permission settings
    ("pre-tool-use", {"decision": "block", "reason": "x"}, {}),  # an unrecognised decision blocks the tool call
    ("pre-tool-use", {"decision": "deny", "reason": "r"}, {"decision": "deny", "reason": "r"}),
    ("pre-tool-use", {"decision": "ask"}, {"decision": "ask", "reason": ""}),
    ("pre-tool-use", {"unrelated": 1}, {}),
    ("post-tool-use", {"decision": "deny", "reason": "r"}, {}),
    ("stop", {"decision": "continue", "reason": "go on"}, {"decision": "continue", "reason": "go on"}),
    ("stop", {"decision": "continue", "reason": "  "}, {}),  # the engine ignores a continuation without a reason
    ("stop", {"decision": "continue"}, {}),
    ("stop", {"decision": "stop"}, {}),
    ("stop", ["not", "a", "dict"], {}),
    ("pre-tool-use", None, {}),
])
def test_contract_squeezes_every_answer_into_the_documented_shapes(event, given, expected):
    assert hooks.contract(event, given) == expected


def test_a_handler_that_returns_junk_still_yields_a_valid_answer(monkeypatch):
    monkeypatch.setitem(hooks.HANDLERS, "pre-tool-use", lambda payload: {"decision": "allow", "extra": [1]})
    assert hooks.run_hook("pre-tool-use", "{}") == "{}"


def test_a_crashing_handler_answers_empty_and_is_counted(monkeypatch, home):
    def boom(payload):
        raise RuntimeError("boom")

    monkeypatch.setitem(hooks.HANDLERS, "stop", boom)
    assert hooks.run_hook("stop", "{}") == "{}"
    summary = hook_health.summary()
    assert summary is not None and summary["counts"] == {"handler_error": 1}


def test_a_broken_guard_yaml_does_not_switch_off_the_builtin_rules(workspace):
    write(paths.workspace_state_dir(workspace) / "guard.yaml", "deny: [\"(unclosed\"]\n")
    payload = {"toolCall": {"name": "run_command", "args": {"CommandLine": "rm -rf /"}},
               "workspacePaths": [str(workspace)]}
    assert json.loads(hooks.run_hook("pre-tool-use", json.dumps(payload)))["decision"] == "deny"
    summary = hook_health.summary()
    assert summary is not None and summary["counts"] == {"guard_yaml_invalid": 1}


def test_guard_yaml_that_is_not_a_mapping_is_reported_not_fatal(workspace):
    write(paths.workspace_state_dir(workspace) / "guard.yaml", "- just\n- a list\n")
    payload = {"toolCall": {"name": "run_command", "args": {"CommandLine": "git reset --hard"}},
               "workspacePaths": [str(workspace)]}
    assert json.loads(hooks.run_hook("pre-tool-use", json.dumps(payload)))["decision"] == "ask"


# --- a stop caused by an error is not a reason to push the agent on --------------------------------------------------


@pytest.mark.parametrize("extra, final", [
    ({"error": "network issue"}, True),
    ({"terminationReason": "error"}, True),
    ({"terminationReason": "max_steps_exceeded"}, True),
    ({"terminationReason": "user_cancelled"}, True),
    ({"terminationReason": "waiting_for_user_input"}, True),
    ({"terminationReason": "request_timeout"}, True),
    ({"terminationReason": "tool_failure"}, True),
    ({"terminationReason": "model_stop", "error": ""}, False),
    ({}, False),
])
def test_stop_is_final_after_an_error_or_a_limit(extra, final):
    assert hooks.stop_is_final(extra) is final


def test_stop_does_not_continue_a_run_after_an_error_but_does_after_a_normal_stop(workspace):
    _active_run(workspace)
    base = {"workspacePaths": [str(workspace)]}
    assert hooks.stop({**base, "terminationReason": "error", "error": "daily-cloudcode-pa unreachable"}) == {}
    assert hooks.stop({**base, "terminationReason": "model_stop"})["decision"] == "continue"


# --- runs nobody is working on do not drag a new conversation back in -------------------------------------------------


def test_a_forgotten_run_neither_nags_nor_collects_ledger_lines(workspace):
    store, state = _active_run(workspace)
    with store.transaction(state.run_id) as s:
        s.updated_at = time.time() - 2 * hooks.STALE_RUN_SECONDS
    payload = {"workspacePaths": [str(workspace)], "toolCall": {"name": "run_command", "args": {"CommandLine": "ls"}}}
    before = len(store.read_ledger(state.run_id))
    assert hooks.stop(payload) == {}
    assert hooks.post_tool_use(payload) == {}
    assert len(store.read_ledger(state.run_id)) == before
    with store.transaction(state.run_id) as s:  # somebody picks it up again: it is live again
        s.updated_at = time.time()
    assert hooks.stop(payload)["decision"] == "continue"


# --- the idle fast path ----------------------------------------------------------------------------------------------


def test_no_active_run_is_proved_only_by_a_matching_index(workspace):
    store = RunStore(workspace)
    assert hooks._no_active_run(workspace) is True, "no runs directory: nothing was ever started here"
    done = make_run(store, "succeeded", 1)
    assert hooks._no_active_run(workspace) is True, "the store indexed it as finished"
    (store.root / "stray-run").mkdir()  # a run directory the index has never seen (e.g. written by another tool)
    assert hooks._no_active_run(workspace) is False, "a run directory the index has not seen is not a proof"
    (store.root / "stray-run").rmdir()
    store.active()  # the full path reconciles the index
    assert hooks._no_active_run(workspace) is True
    make_run(store, "running", 2)
    assert hooks._no_active_run(workspace) is False
    store.active()
    assert hooks._no_active_run(workspace) is False
    index = store.root / paths.RUN_INDEX_FILE
    with store.transaction(done.run_id):
        pass
    good = index.read_text(encoding="utf-8")
    for damaged in ("{not json", json.dumps({"version": 99, "runs": {}}), json.dumps({"version": 1, "runs": []})):
        index.write_text(damaged, encoding="utf-8")
        assert hooks._no_active_run(workspace) is False, damaged
    index.write_text(good, encoding="utf-8")
    entries = json.loads(good)["runs"]
    entries[done.run_id]["corrupt"] = True
    index.write_text(json.dumps({"version": 1, "runs": entries}), encoding="utf-8")
    assert hooks._no_active_run(workspace) is False


def test_the_idle_fast_path_agrees_with_the_run_store_in_every_state(workspace):
    """Whatever the fast path says, the store must agree: 'idle' is only ever true when nothing is running."""
    store = RunStore(workspace)
    for n, status in enumerate(["succeeded", "failed", "aborted", "succeeded"]):
        make_run(store, status, n)
    store.active()
    assert hooks._no_active_run(workspace) is True and store.active() == []
    make_run(store, "running", 10)
    store.active()
    assert hooks._no_active_run(workspace) is False and len(store.active()) == 1


def test_a_hook_in_an_idle_workspace_loads_neither_the_store_nor_pydantic_nor_a_yaml_parser(workspace):
    heavy = ["agflow.graph", "agflow.graph.store", "agflow.graph.engine", "agflow.fsutil", "pydantic", "jinja2",
             "ruamel", "filelock", "urllib.request", "http.client", "traceback"]
    code = (
        "import io, sys\n"
        "from agflow import hooks\n"
        f"sys.stdin = io.TextIOWrapper(io.BytesIO({json.dumps({'workspacePaths': [str(workspace)]}).encode()!r}))\n"
        "for event in ('post-tool-use', 'stop'):\n"
        "    sys.stdin = io.TextIOWrapper(io.BytesIO(b'{\"workspacePaths\": [\"' + sys.argv[1].encode() + b'\"]}'))\n"
        "    hooks.run_hook(event)\n"
        f"print(sorted(m for m in {heavy!r} if m in sys.modules))\n"
    )
    argv = [sys.executable, "-c", code, str(workspace).replace("\\", "/")]
    done = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "[]", f"the idle path imported: {done.stdout.strip()}"


def test_the_process_answers_an_idle_hook_quickly_enough_to_run_on_every_tool_call(workspace):
    """Not a benchmark (machines differ): a loose ceiling that the pre-H1 hook (~0.3-0.65 s here) would not have met
    on a loaded CI box either, so it only guards against the order-of-magnitude regression of importing the store."""
    payload = json.dumps({"workspacePaths": [str(workspace)]}).encode()
    run_cli("post-tool-use", payload)  # warm the bytecode caches
    start = time.perf_counter()
    done = run_cli("post-tool-use", payload)
    elapsed = time.perf_counter() - start
    assert done.returncode == 0 and done.stdout.strip() == b"{}"
    assert elapsed < 3.0, f"an idle hook took {elapsed:.2f}s"


# --- the process contract --------------------------------------------------------------------------------------------


def test_the_process_exits_0_with_exactly_one_json_line_even_for_garbage():
    for stdin in (b"", b"not json", b"[1]", b"\xff\xfe\x00 binary \x9d"):
        done = run_cli("pre-tool-use", stdin)
        assert done.returncode == 0, done.stderr
        assert [json.loads(line) for line in done.stdout.decode().splitlines()] == [{}], stdin


def test_the_workspace_path_survives_non_ascii_characters(home, tmp_path):
    """stdin was decoded with the console code page (cp1252 on Windows): a Persian folder name arrived as mojibake, no
    workspace was found, and the ledger silently stayed empty."""
    workspace = tmp_path / "پروژه-ws"
    (workspace / ".git").mkdir(parents=True)
    store, state = _active_run(workspace)
    payload = {"workspacePaths": [str(workspace)], "toolCall": {"name": "run_command",
                                                                  "args": {"CommandLine": "echo سلام"}}}
    done = run_cli("post-tool-use", json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    assert done.returncode == 0 and done.stdout.strip() == b"{}", done.stderr
    assert store.read_ledger(state.run_id)[-1]["event"] == "tool"


def test_a_hook_that_hangs_is_answered_by_the_watchdog_before_antigravity_would_time_it_out(tmp_path):
    code = (
        "import sys, threading\n"
        "from agflow import hooks\n"
        "hooks.HANDLERS['stop'] = lambda payload: threading.Event().wait()  # blocks: only the watchdog ends it\n"
        "sys.exit(hooks.main('stop', budget=0.5))\n"
    )
    start = time.perf_counter()
    done = subprocess.run([sys.executable, "-c", code], input=b"{}", capture_output=True, timeout=30, check=False)
    assert done.returncode == 0 and done.stdout.strip() == b"{}", done.stderr
    assert time.perf_counter() - start < 10


def test_the_budget_is_below_the_timeout_the_installed_hooks_declare():
    data = json.loads((Path(__file__).resolve().parents[1] / "plugin" / "hooks.json").read_text(encoding="utf-8"))
    declared = {h["timeout"] for spec in data.values() for entries in spec.values() for e in entries
                for h in e.get("hooks", [e])}
    assert declared and min(declared) > hooks.HOOK_BUDGET_SECONDS


# --- the probe `agflow doctor` uses -----------------------------------------------------------------------------------


def test_the_probe_succeeds_on_a_healthy_install_and_touches_no_run(workspace):
    store, state = _active_run(workspace)
    before = (store.run_dir(state.run_id) / "state.json").read_bytes()
    for event in hooks.HANDLERS:
        done = run_cli(event, b"{}", **{hooks.PROBE_ENV: "1"})
        assert done.returncode == 0 and done.stdout.strip() == b"{}", (event, done.stderr)
    assert (store.run_dir(state.run_id) / "state.json").read_bytes() == before
    assert hook_health.summary() is None, "a probe is not a payload the guard failed to understand"


def test_the_probe_fails_loudly_when_the_install_cannot_load(monkeypatch, capsys):
    import importlib

    real = importlib.import_module

    def broken(name, *a, **k):
        if name == "agflow.graph.store":
            raise ModuleNotFoundError("No module named 'pydantic'")
        return real(name, *a, **k)

    monkeypatch.setenv(hooks.PROBE_ENV, "1")
    monkeypatch.setattr(importlib, "import_module", broken)
    assert hooks.main("stop") == 1
    assert "No module named 'pydantic'" in capsys.readouterr().err


# --- file:// workspaces without urllib.request ------------------------------------------------------------------------


@pytest.mark.parametrize("uri, windows, expected", [
    ("file:///C:/Users/a%20b/proj", True, "C:/Users/a b/proj"),
    ("file:///home/a%20b/proj", False, "/home/a b/proj"),
    ("file://server/share/x", True, "//server/share/x"),
    ("file://localhost/C:/x", True, "C:/x"),
])
def test_file_uris_become_paths(monkeypatch, uri, windows, expected):
    monkeypatch.setattr(hooks.os, "name", "nt" if windows else "posix")
    assert hooks.file_uri_to_path(uri) == expected
