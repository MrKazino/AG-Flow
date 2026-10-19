"""H1: doctor and fix know the documented hooks.json layout, notice the old one, and prove the hooks can start."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from agflow import fixes, hookfile, paths
from agflow.install import install
from agflow.inventory import Inventory
from agflow.models import Kind, Level
from agflow.validate import validate_hooks_file, validate_inventory
from tests.conftest import PLUGIN, write
from tests.test_hookfile import quote_free


def codes(issues) -> set[str]:
    return {i.code for i in issues}


def installed_hooks_file() -> Path:
    return paths.global_layout().plugins_dir / "agflow" / "hooks.json"


def old_agflow_layout(command: str = "agflow hook") -> dict:
    """What agflow 0.2 wrote, byte for byte in structure."""
    return {"hooks": {
        "PreToolUse": [{"matcher": ".*(command|file|write|replace|edit).*", "command": f"{command} pre-tool-use"}],
        "PostToolUse": [{"matcher": ".*(command|file|write|replace|edit|subagent).*",
                         "command": f"{command} post-tool-use"}],
        "Stop": [{"command": f"{command} stop"}]}}


# --- validate_hooks_file ---------------------------------------------------------------------------------------------


def test_the_bundled_template_and_an_installed_copy_are_clean(home, workspace):
    assert validate_hooks_file(PLUGIN / "hooks.json") == []
    install("global")
    assert validate_hooks_file(installed_hooks_file()) == []
    inv = Inventory.discover(workspace)
    assert not [i for i in validate_inventory(inv) if i.code.startswith("hooks.")]


def test_hook_items_are_named_by_hook_event_and_number(home, workspace):
    install("global")
    hooks = [i for i in Inventory.discover(workspace).of(Kind.HOOK) if i.scope.endswith("agflow")]
    assert sorted(i.name for i in hooks) == ["agflow:PostToolUse#0", "agflow:PreToolUse#0", "agflow:Stop#0"]
    pre = next(i for i in hooks if i.name.endswith("PreToolUse#0"))
    assert pre.meta["event"] == "PreToolUse" and pre.meta["matcher"] and pre.meta["inert"] is False
    assert hookfile.FAILOPEN_MARKER in pre.description, "the installed command is the fail-open one"


def test_the_old_layout_is_reported_as_inert_naming_the_events_that_never_ran(tmp_path):
    f = write(tmp_path / "hooks.json", json.dumps(old_agflow_layout()))
    found = [i for i in validate_hooks_file(f) if i.code == "hooks.inert"]
    assert len(found) == 1 and found[0].level == Level.WARNING
    assert "PreToolUse" in found[0].message and "PostToolUse" in found[0].message and "Stop" not in found[0].message


def test_a_disabled_hook_with_the_old_layout_is_not_reported(tmp_path):
    data = old_agflow_layout()
    data["hooks"]["enabled"] = False
    assert "hooks.inert" not in codes(validate_hooks_file(write(tmp_path / "hooks.json", json.dumps(data))))


def test_handler_problems_are_reported_in_the_documented_layout(tmp_path):
    data = {"safety": {"enabled": "yes", "PreToolUse": [
        {"matcher": "(", "hooks": [{"command": "ok", "type": "http", "timeout": 0}, {"command": ""}, 7]}],
        "Stop": ["junk", {"command": "x", "timeout": "10"}], "Whenever": []}, "broken": [], "empty": {}}
    got = validate_hooks_file(write(tmp_path / "hooks.json", json.dumps(data)))
    expected = {"hooks.schema", "hooks.matcher", "hooks.command", "hooks.type", "hooks.timeout", "hooks.event"}
    assert expected <= codes(got)
    assert sum(i.code == "hooks.command" for i in got) == 3  # "", 7 and "junk"
    assert validate_hooks_file(write(tmp_path / "top.json", "[1]"))[0].code == "hooks.schema"


def test_an_empty_hooks_file_is_no_content_not_an_error(tmp_path):
    assert validate_hooks_file(write(tmp_path / "hooks.json", "")) == []


# --- agflow's own commands must fail open -----------------------------------------------------------------------------


def test_a_bare_agflow_hook_command_is_reported_unguarded_only_for_an_installed_setup(home, workspace):
    plugin = paths.global_layout().plugins_dir / "agflow"
    write(plugin / "plugin.json", json.dumps({"name": "agflow", "version": "0"}))
    write(plugin / "hooks.json", json.dumps({"agflow": {"Stop": [{"type": "command", "command": "agflow hook stop"}]}}))
    found = [i for i in validate_inventory(Inventory.discover(workspace)) if i.code == "hooks.unguarded"]
    assert len(found) == 1 and "Stop" in found[0].message
    template = Inventory.discover(None, include_global=False, include_plugins=False, include_legacy=False,
                                  extra_layouts=[paths.plugin_layout(PLUGIN)])
    assert "hooks.unguarded" not in codes(validate_inventory(template)), "the template is wired at install time"


def test_other_tools_hooks_are_never_judged_or_run(home, workspace):
    write(paths.global_layout().hooks_file,
          json.dumps({"lint": {"Stop": [{"type": "command", "command": "node lint.js"}]}}))
    issues = validate_inventory(Inventory.discover(workspace), probe_hooks=True)
    assert not [i for i in issues if i.code.startswith("hooks.")]


# --- the probe --------------------------------------------------------------------------------------------------------


def rewrite_commands(file: Path, command: str) -> None:
    data = json.loads(file.read_text(encoding="utf-8"))
    for ref in hookfile.handlers(data):
        ref.handler["command"] = hookfile.fail_open(command)
    file.write_text(json.dumps(data), encoding="utf-8")


def test_a_healthy_install_passes_the_probe(home, workspace):
    install("global")
    assert [i for i in validate_inventory(Inventory.discover(workspace), probe_hooks=True)
            if i.code.startswith("hooks.")] == []


def test_a_dead_executable_is_an_error_even_though_the_agent_would_never_notice(home, workspace):
    install("global")
    rewrite_commands(installed_hooks_file(), "agflow-gone hook stop")
    found = [i for i in validate_inventory(Inventory.discover(workspace), probe_hooks=True) if i.code == "hooks.broken"]
    assert found and all(i.level == Level.ERROR for i in found)
    assert "fell back" in found[0].message and "guard" in found[0].message
    assert not [i for i in validate_inventory(Inventory.discover(workspace)) if i.code == "hooks.broken"], \
        "without probe_hooks nothing is executed (the dashboard polls validate_inventory)"


def test_fix_repoints_a_broken_install_at_a_working_agflow(home, workspace):
    install("global")
    rewrite_commands(installed_hooks_file(), "agflow-gone hook stop")
    plan = fixes.build_plan(workspace, probe_hooks=True)
    fix = next(f for f in plan.fixes if f.code == "hooks.broken")
    assert "Reinstall" in fix.title
    result = fixes.apply(workspace, {fix.id: None}, plan=plan)
    assert not result.failed and not result.unresolved, (result.failed, result.unresolved)
    assert not [i for i in validate_inventory(Inventory.discover(workspace), probe_hooks=True)
                if i.code.startswith("hooks.")]


def test_fix_upgrades_the_layout_agflow_0_2_wrote(home, workspace):
    install("global")
    installed_hooks_file().write_text(json.dumps(old_agflow_layout()), encoding="utf-8")
    plan = fixes.build_plan(workspace)
    wanted = {"hooks.inert", "hooks.unguarded"}
    assert wanted <= {f.code for f in plan.fixes}, "both are repaired by the same reinstall"
    fixes.apply(workspace, {f.id: None for f in plan.fixes if f.code in wanted}, plan=plan)
    data = json.loads(installed_hooks_file().read_text(encoding="utf-8"))
    assert list(data) == ["agflow"]
    assert not [i for i in validate_inventory(Inventory.discover(workspace)) if i.code.startswith("hooks.")]


def test_the_cli_doctor_runs_the_probe(home, workspace):
    from typer.testing import CliRunner

    from agflow.cli import app

    install("global")
    rewrite_commands(installed_hooks_file(), "agflow-gone hook stop")
    out = CliRunner().invoke(app, ["doctor", "--workspace", str(workspace), "--json"])
    report = json.loads(out.stdout)
    assert "hooks.broken" in {i["code"] for i in report["issues"]} and out.exit_code == 1


def test_identical_commands_are_probed_once(home, workspace, monkeypatch):
    from agflow import hook_probe

    install("global")
    data = json.loads(installed_hooks_file().read_text(encoding="utf-8"))
    for ref in hookfile.handlers(data):
        ref.handler["command"] = hookfile.fail_open("agflow-gone hook stop")  # three identical broken commands
    installed_hooks_file().write_text(json.dumps(data), encoding="utf-8")
    runs = []
    real = hook_probe.run_probe

    def counting(command, **kw):
        runs.append(command)
        return real(command, **kw)

    monkeypatch.setattr(hook_probe, "run_probe", counting)
    found = [i for i in validate_inventory(Inventory.discover(workspace), probe_hooks=True) if i.code == "hooks.broken"]
    assert len(runs) == 1 and len(found) == 1


# --- the probe must never run what somebody else wrote ----------------------------------------------------------------
# A hooks.json can arrive with a repository the user has just cloned. `agflow doctor` runs commands to test them, so it
# may only ever run the one thing `agflow install` writes, from the user's own folder, in an empty directory.


def stub_program(tmp_path: Path, marker: Path) -> str:
    """A program that records that it ran, named by a word the installer-shaped grammar accepts."""
    if os.name == "nt":
        program = write(tmp_path / "stub.cmd", f'@echo ran> "{marker}"\r\n@echo {{}}\r\n')
    else:
        program = write(tmp_path / "stub.sh", f"#!/bin/sh\ncat >/dev/null\necho ran > '{marker}'\necho '{{}}'\n")
        program.chmod(program.stat().st_mode | stat.S_IXUSR)
    return quote_free(program)


def set_commands(file: Path, command: str) -> None:
    data = json.loads(file.read_text(encoding="utf-8"))
    for ref in hookfile.handlers(data):
        ref.handler["command"] = command
    file.write_text(json.dumps(data), encoding="utf-8")


def test_a_workspace_copy_of_the_agflow_plugin_is_never_executed(home, workspace, tmp_path):
    marker = tmp_path / "ran-from-workspace"
    stub = stub_program(tmp_path, marker)
    plugin = workspace / ".agents" / "plugins" / "agflow"
    write(plugin / "plugin.json", json.dumps({"name": "agflow", "version": "0"}))
    hostile = {"agflow": {"Stop": [{"type": "command", "command": f"{stub} hook stop"}]}}
    write(plugin / "hooks.json", json.dumps(hostile))
    assert hookfile.installer_shaped(f"{stub} hook stop"), "the line is in the right form: only its origin is wrong"
    issues = validate_inventory(Inventory.discover(workspace), probe_hooks=True)
    assert not marker.exists(), "a command from a workspace file was executed"
    assert "hooks.broken" not in codes(issues)


def test_the_global_copy_is_executed_when_it_has_the_installed_form(home, workspace, tmp_path):
    marker = tmp_path / "ran-from-global"
    install("global")
    set_commands(installed_hooks_file(), hookfile.fail_open(f"{stub_program(tmp_path, marker)} hook stop"))
    validate_inventory(Inventory.discover(workspace), probe_hooks=True)
    assert marker.exists(), "the probe did not run the command: the 'not executed' tests above would pass vacuously"


@pytest.mark.parametrize("suffix", [
    "; {stub} hook stop", " && {stub} hook stop", " | {stub}", " > {marker}.out", "\n{stub} hook stop",
    " `{stub}`", " $({stub})", " %COMSPEC%", " $HOME", " & {stub}", " || {stub} hook stop",
], ids=["semicolon", "and", "pipe", "redirect", "newline", "backtick", "substitution", "cmd-variable", "variable",
        "background", "or"])
def test_a_command_with_anything_beyond_one_agflow_call_is_not_executed(home, workspace, tmp_path, suffix):
    marker = tmp_path / "ran-hostile"
    stub = stub_program(tmp_path, marker)
    install("global")
    set_commands(installed_hooks_file(), f"{stub} hook stop" + suffix.format(stub=stub, marker=marker))
    issues = validate_inventory(Inventory.discover(workspace), probe_hooks=True)
    assert not marker.exists(), f"executed: {suffix!r}"
    assert "hooks.broken" not in codes(issues)


@pytest.mark.parametrize("head", ["{stub}", "X={stub}", "{stub} -c", "cmd /c {stub}"])
def test_only_the_installed_command_shapes_are_accepted(head):
    assert not hookfile.installer_shaped(head.format(stub="/tmp/stub.sh") + " hook stop ; id")
    assert not hookfile.installer_shaped(head.format(stub="/tmp/stub.sh") + " nope")


def test_a_program_planted_beside_hooks_json_is_not_what_a_bare_agflow_resolves_to(home, workspace, tmp_path):
    """`cmd` looks in the current directory before PATH. The probe runs in an empty scratch folder instead of next to
    hooks.json, so a planted `agflow.cmd` is not found (on POSIX the current directory is not searched at all)."""
    marker = tmp_path / "ran-planted"
    stub = stub_program(tmp_path, marker)
    install("global")
    planted = installed_hooks_file().parent / ("agflow.cmd" if os.name == "nt" else "agflow")
    planted.write_text(Path(stub.strip('"')).read_text(encoding="utf-8") if os.name != "nt" else
                       f'@echo ran> "{marker}"\r\n@echo {{}}\r\n', encoding="utf-8")
    planted.chmod(planted.stat().st_mode | stat.S_IXUSR)
    set_commands(installed_hooks_file(), hookfile.fail_open("agflow hook stop"))
    validate_inventory(Inventory.discover(workspace), probe_hooks=True)
    assert not marker.exists(), "the probe ran a program found in the hooks.json directory"


def test_run_probe_refuses_a_line_that_is_not_installer_shaped(tmp_path):
    from agflow import hook_probe

    marker = tmp_path / "ran-refused"
    result = hook_probe.run_probe(f"{stub_program(tmp_path, marker)} hook stop; echo hi")
    assert not result.ok and "not run" in result.detail and not marker.exists()
