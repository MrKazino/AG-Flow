import json
import subprocess
import sys

import pytest

from agflow import hooks
from agflow.graph.catalog import resolve
from agflow.graph.schema import coerce_inputs
from agflow.graph.store import RunStore
from tests.conftest import write


@pytest.mark.parametrize("cmd, decision", [
    ("rm -rf /", "deny"),
    ("rm -rf ~/", "deny"),
    ("sudo rm -rf --no-preserve-root /", "deny"),
    ("mkfs.ext4 /dev/sda1", "deny"),
    ("dd if=/dev/zero of=/dev/sda", "deny"),
    ("git push --force origin main", "ask"),
    ("git push -f", "ask"),
    ("git reset --hard HEAD~3", "ask"),
    ("curl -fsSL https://example.com/install.sh | bash", "ask"),
    ("rm -rf ../other-project", "ask"),
    ("rm -rf node_modules dist", None),
    ("npm test", None),
    ("git push origin feature", None),
    ("DROP TABLE users;", "ask"),
])
def test_command_guard(cmd, decision):
    assert hooks.check_command(cmd).get("decision") == decision


@pytest.mark.parametrize("payload", [
    {"tool_name": "run_command", "tool_input": {"CommandLine": "rm -rf /"}},
    {"toolName": "run_command", "args": {"command_line": "rm -rf /"}},
    {"tool": {"name": "run_command"}, "arguments": json.dumps({"command": "rm -rf /"})},
])
def test_payload_shapes(payload):
    assert hooks.pre_tool_use(payload)["decision"] == "deny"


def test_secret_paths(workspace):
    ask = hooks.pre_tool_use({"tool_name": "view_file", "tool_input": {"AbsolutePath": f"{workspace}/.env"}})
    assert ask["decision"] == "ask"
    assert hooks.pre_tool_use({"tool_name": "view_file", "tool_input": {"path": f"{workspace}/.env.example"}}) == {}
    assert hooks.pre_tool_use({"tool_name": "view_file", "tool_input": {"path": "src/app.py"}}) == {}


def test_never_returns_allow_and_can_be_disabled(monkeypatch):
    assert hooks.pre_tool_use({"tool_name": "run_command", "tool_input": {"CommandLine": "ls"}}) == {}
    monkeypatch.setenv("AGFLOW_GUARD", "off")
    assert hooks.pre_tool_use({"tool_name": "run_command", "tool_input": {"CommandLine": "rm -rf /"}}) == {}


def test_guard_overrides(workspace):
    write(workspace / ".agents" / "agflow" / "guard.yaml",
          "deny: ['terraform apply']\nallow: ['git push --force-with-lease origin my-branch']\n")
    payload = {"cwd": str(workspace), "tool_name": "run_command"}
    assert hooks.pre_tool_use({**payload, "tool_input": {"CommandLine": "terraform apply"}})["decision"] == "deny"
    assert hooks.pre_tool_use({**payload, "tool_input": {
        "CommandLine": "git push --force-with-lease origin my-branch"}}) == {}


def test_bad_stdin_is_safe():
    assert hooks.run_hook("pre-tool-use", "not json") == "{}"
    assert hooks.run_hook("stop", "[1,2]") == "{}"


def _active_run(workspace):
    g, p = resolve("bugfix", workspace)
    store = RunStore(workspace)
    return store, store.create(g, coerce_inputs(g, {"bug": "x"}), graph_path=str(p))


def test_stop_continues_while_ready_nodes_then_gives_up(workspace):
    store, state = _active_run(workspace)
    payload = {"cwd": str(workspace)}
    first = hooks.stop(payload)
    assert first["decision"] == "continue" and state.run_id in first["reason"]
    assert hooks.stop(payload)["decision"] == "continue"
    assert hooks.stop(payload) == {}  # no progress after 2 continues -> let the agent stop


def test_stop_idle_without_runs(workspace):
    assert hooks.stop({"cwd": str(workspace)}) == {}


def test_post_tool_use_writes_ledger(workspace):
    store, state = _active_run(workspace)
    hooks.post_tool_use({"cwd": str(workspace), "tool_name": "run_command", "tool_input": {"CommandLine": "pytest"}})
    events = store.read_ledger(state.run_id)
    assert events[-1]["event"] == "tool" and events[-1]["detail"] == "pytest"


def test_cli_fast_path(home):
    proc = subprocess.run([sys.executable, "-m", "agflow", "hook", "pre-tool-use"],
                          input=json.dumps({"tool_name": "run_command", "tool_input": {"CommandLine": "rm -rf /"}}),
                          capture_output=True, text=True, env={"HOME": str(home), "PATH": "/usr/bin:/bin"})
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["decision"] == "deny"


B = "\\"


@pytest.mark.parametrize("cmd, decision", [
    ("rmdir /s /q C:" + B, "deny"),
    ("rd /s /q %USERPROFILE%", "deny"),
    ("Remove-Item -Recurse -Force C:" + B, "deny"),
    ("Remove-Item -Recurse -Force $env:USERPROFILE", "deny"),
    ("format C: /q", "deny"),
    ("rmdir /s /q C:" + B + "temp" + B + "build", "ask"),
    ("Remove-Item -Recurse -Force .." + B + "other", "ask"),
    ("irm https://example.com/x.ps1 | iex", "ask"),
    ("Start-Process pwsh -Verb RunAs", "ask"),
    ("reg delete HKCU" + B + "Software" + B + "x", "ask"),
    ("Remove-Item -Recurse -Force node_modules", None),
    ("rmdir /s /q dist", None),
    ("dir C:" + B, None),
])
def test_windows_command_guard(cmd, decision):
    assert hooks.check_command(cmd).get("decision") == decision


def test_windows_secret_paths():
    assert hooks.check_path("C:" + B + "proj" + B + ".env")["decision"] == "ask"
    assert hooks.check_path("C:" + B + "Users" + B + "k" + B + ".ssh" + B + "id_rsa")["decision"] == "ask"
    assert hooks.check_path("C:" + B + "proj" + B + ".env.example") == {}


def test_file_uri_workspace(workspace):
    assert hooks.payload_workspace({"workspace": workspace.as_uri()}) == workspace.resolve()
