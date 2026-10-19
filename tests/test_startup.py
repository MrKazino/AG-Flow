"""Start-up cost is guarded by what gets imported, not by a stopwatch: a clock flakes on a busy runner, a module that
should not load does not. Each check starts a fresh interpreter and looks at ``sys.modules``."""

from __future__ import annotations

import json
import subprocess
import sys

from agflow import __version__, hook_probe, platform_vocab
from agflow.models import Issue, Kind
from tests.conftest import REPO


def loaded_after(statement: str) -> set[str]:
    """The modules a fresh interpreter has loaded after ``statement``."""
    code = f"import sys\n{statement}\nprint(__import__('json').dumps(sorted(sys.modules)))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, cwd=REPO,
                         env={"PYTHONPATH": str(REPO / "src"), "PATH": "", "SYSTEMROOT": "C:/Windows"}, check=True)
    return set(json.loads(out.stdout.strip().splitlines()[-1]))


def test_the_cli_does_not_load_asyncio_until_a_graph_runs():
    assert "asyncio" not in loaded_after("import agflow.cli")


def test_version_needs_neither_typer_nor_rich_nor_the_cli():
    mods = loaded_after("import sys; sys.argv = ['agflow', 'version']; import agflow.__main__ as m; m.main()")
    assert not {"agflow.cli", "typer", "rich", "asyncio", "pydantic"} & mods


def test_version_prints_exactly_what_the_command_prints():
    quick = subprocess.run([sys.executable, "-m", "agflow", "version"], capture_output=True, text=True, timeout=60,
                           cwd=REPO, env={"PYTHONPATH": str(REPO / "src"), "SYSTEMROOT": "C:/Windows"})
    assert quick.returncode == 0 and quick.stdout.strip() == f"agflow {__version__}"
    from typer.testing import CliRunner

    from agflow.cli import app

    assert CliRunner().invoke(app, ["version"]).output.strip() == quick.stdout.strip()


def test_other_commands_are_not_sent_down_the_fast_path():
    # the fast path is exactly ["version"]: anything else is Typer's to judge
    helped = subprocess.run([sys.executable, "-m", "agflow", "version", "--help"], capture_output=True, text=True,
                            timeout=60, cwd=REPO, env={"PYTHONPATH": str(REPO / "src"), "SYSTEMROOT": "C:/Windows"})
    assert "Usage" in helped.stdout and helped.stdout.strip() != f"agflow {__version__}"


MANAGER_ONLY = {"agflow.workflows", "agflow.improve", "agflow.maintenance", "agflow.sidecars", "agflow.update",
                "agflow.update_apply", "agflow.compat", "agflow.antigravity_probe", "agflow.release_feed",
                "agflow.dashboard", "agflow.dashboard.app", "agflow.cli"}


def test_the_mcp_server_loads_the_managers_modules_only_when_their_tool_is_called():
    mods = loaded_after("import agflow.mcp_server")
    assert not MANAGER_ONLY & mods, sorted(MANAGER_ONLY & mods)


def test_the_cli_loads_the_updater_and_the_manager_only_for_their_commands():
    mods = loaded_after("import agflow.cli")
    lazy = {"agflow.update", "agflow.update_apply", "agflow.compat", "agflow.antigravity_probe", "agflow.release_feed",
            "agflow.workflows", "agflow.improve", "agflow.maintenance", "agflow.sidecars", "agflow.dashboard.app",
            "agflow.learn", "fastapi", "uvicorn", "mcp"}
    assert not lazy & mods, sorted(lazy & mods)


def test_the_hook_fast_path_stays_small():
    mods = loaded_after("import agflow.hooks")
    heavy = {"agflow.cli", "agflow.mcp_server", "agflow.dashboard.app", "agflow.update", "agflow.compat",
             "agflow.learn", "agflow.workflows", "agflow.improve", "typer", "rich", "asyncio", "fastapi", "mcp",
             "pydantic"}
    assert not heavy & mods, sorted(heavy & mods)


# --- the doctor's hook probes run side by side ----------------------------------------------------


def test_probes_run_in_parallel_and_report_in_a_stable_order(workspace, home, monkeypatch):
    from agflow import install as inst
    from agflow.inventory import Inventory

    inst.install("global", with_gemini_md=False)
    inv = Inventory.discover(workspace)
    started: list[str] = []

    import threading

    barrier = threading.Barrier(3, timeout=10)   # all three probes must be in flight at once to pass the barrier

    def fake_probe(command, *, timeout=hook_probe.TIMEOUT_SECONDS):
        started.append(command)
        barrier.wait()
        if "stop" in command:
            return hook_probe.ProbeResult(False, f"failed: {command[-30:]}")
        return hook_probe.ProbeResult(True)

    monkeypatch.setattr(hook_probe, "run_probe", fake_probe)
    issues = hook_probe.probe_issues(inv)
    assert len(started) == 3, "one probe per installed hook command"
    assert [i.code for i in issues] == ["hooks.broken"] and "Stop hook" in issues[0].message


def test_a_failing_probe_does_not_hide_a_passing_one(workspace, home, monkeypatch):
    from agflow import install as inst
    from agflow.inventory import Inventory

    inst.install("global", with_gemini_md=False)
    inv = Inventory.discover(workspace)
    monkeypatch.setattr(hook_probe, "run_probe", lambda command, **kw: hook_probe.ProbeResult(
        "pre-tool-use" not in command, "could not start"))
    issues = hook_probe.probe_issues(inv)
    assert [i.code for i in issues] == ["hooks.broken"] and "PreToolUse hook could not start" in issues[0].message
    assert all(isinstance(i, Issue) for i in issues)


def test_no_probe_needed_means_no_threads(workspace, home):
    from agflow.inventory import Inventory

    assert hook_probe.probe_issues(Inventory.discover(workspace)) == []
    assert Kind.HOOK is not None and platform_vocab.builtin().hook_events


def test_the_version_fast_path_prints_in_process(monkeypatch, capsys):
    import agflow.__main__ as entry

    monkeypatch.setattr(sys, "argv", ["agflow", "version"])
    entry.main()
    assert capsys.readouterr().out.strip() == f"agflow {__version__}"
