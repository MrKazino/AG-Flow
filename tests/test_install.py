import json
import os
from pathlib import Path

import pytest

from agflow import frontmatter, hookfile, paths
from agflow.install import BEGIN, InstallError, install, uninstall
from agflow.inventory import Inventory
from agflow.models import Kind
from agflow.validate import validate_inventory
from tests.conftest import write


def test_global_install_merges_and_uninstall_restores(home):
    user_md = write(home / ".gemini" / "GEMINI.md", "# Mine\n- Prefer pnpm.\n")
    existing_hooks = write(home / ".gemini" / "config" / "plugins" / "agflow" / "old.txt", "old")
    plan = install("global")
    target = paths.global_layout().plugins_dir / "agflow"
    assert (target / "plugin.json").is_file()
    assert not (target / "GEMINI.md").exists()
    assert not (target / "old.txt").exists()
    text = user_md.read_text()
    assert text.startswith("# Mine") and BEGIN in text and "Expert Senior Workflows Manager" in text
    assert len(text) < paths.GEMINI_MD_CHAR_LIMIT
    mcp = json.loads((target / "mcp_config.json").read_text())["mcpServers"]["agflow"]
    assert mcp["args"][-2:] == ["mcp", "serve"]
    hooks = json.loads((target / "hooks.json").read_text())["agflow"]  # Antigravity's documented layout (H1)
    pre = hooks["PreToolUse"][0]
    assert pre["matcher"] and pre["hooks"][0]["type"] == "command" and pre["hooks"][0]["timeout"] > 0
    assert "hook pre-tool-use" in pre["hooks"][0]["command"]
    assert hooks["Stop"][0]["command"].count("hook stop") == 1  # events without a tool hold plain handlers
    assert any("backup" in a for a in plan.actions)

    # idempotent re-install keeps the original backups
    install("global")
    uninstall("global")
    assert user_md.read_text() == "# Mine\n- Prefer pnpm.\n"
    assert existing_hooks.read_text() == "old"  # previous plugin dir restored


def test_install_is_valid_for_doctor(home, workspace):
    install("global")
    issues = [i for i in validate_inventory(Inventory.discover(workspace)) if i.level.value != "info"]
    assert issues == []
    inv = Inventory.discover(workspace)
    assert inv.find(Kind.SKILL, "tdd").scope == "plugin:agflow"


def test_fresh_home_uninstall_removes_everything(home):
    install("global")
    uninstall("global")
    assert not (home / ".gemini" / "GEMINI.md").exists()
    assert not (paths.global_layout().plugins_dir / "agflow").exists()
    with pytest.raises(InstallError):
        uninstall("global")


def test_oversized_gemini_md_falls_back_to_plugin_rule(home):
    write(home / ".gemini" / "GEMINI.md", "x" * 9000)
    plan = install("global")
    assert plan.warnings and "instead" in plan.warnings[0]
    assert (home / ".gemini" / "GEMINI.md").read_text() == "x" * 9000
    rule = paths.global_layout().plugins_dir / "agflow" / "rules" / "agflow-master.md"
    assert frontmatter.parse(rule.read_text())[0]["trigger"] == "always_on"


def test_workspace_install_with_profile(workspace):
    plan = install("workspace", workspace, profile="frontend")
    target = workspace / ".agents" / "plugins" / "agflow"
    skills = {p.name for p in (target / "skills").iterdir()}
    agents = {p.stem for p in (target / "agents").iterdir()}
    assert {"ui-build-verify", "a11y-audit", "tdd", "graph-run"} <= skills
    assert "ci-fix" not in skills and "security-audit" not in skills
    assert agents == {"orchestrator", "architect", "implementer", "tester", "reviewer", "verifier", "researcher",
                      "ui-verifier"}
    roster = frontmatter.parse((target / "agents" / "orchestrator.md").read_text())[0]["agents"]
    assert "./devops.md" not in roster and "./ui-verifier.md" in roster
    master = workspace / ".agents" / "rules" / "agflow-master.md"
    assert frontmatter.parse(master.read_text())[0]["trigger"] == "always_on"
    assert any("not in profile" in a for a in plan.actions)
    graphs = {p.stem for p in (target / "graphs").iterdir()}
    assert graphs == {"bugfix", "ui-feature"}  # the bundled graphs fully covered by frontend (+core)
    issues = [i for i in validate_inventory(Inventory.discover(workspace, include_global=False))
              if i.level.value == "error"]
    assert issues == []
    uninstall("workspace", workspace)
    assert not target.exists() and not master.exists()


def test_dry_run_changes_nothing(home):
    plan = install("global", dry_run=True)
    assert plan.actions
    assert not (home / ".gemini").exists()


def test_link_install(home, plugin_dir, require_symlinks):
    install("global", link=True, with_gemini_md=False)
    target = paths.global_layout().plugins_dir / "agflow"
    assert target.is_symlink() and target.resolve() == plugin_dir.resolve()
    uninstall("global")
    assert not target.exists() and plugin_dir.exists()


def test_no_hooks(home):
    install("global", with_hooks=False)
    assert not (paths.global_layout().plugins_dir / "agflow" / "hooks.json").exists()


def test_format_command_windows_paths(monkeypatch):
    from agflow import install as inst

    spaced = r"C:\Users\Koosha Kazino\.local\bin\agflow.exe"
    monkeypatch.setattr(inst.shutil, "which", lambda name: None)
    monkeypatch.setattr(inst, "_windows_short_path", lambda path: None)  # the real machine's 8.3 names stay out
    # never POSIX single quotes on Windows (cmd.exe can't run them)
    assert inst.format_command([spaced, "hook", "stop"], windows=True) == f'"{spaced}" hook stop'
    # a quote-free 8.3 name beats quotes (the hook runner passes the line to `cmd /c` as one quoted argument)
    short = r"C:\Users\KOOSHA~1\.local\bin\agflow.exe"
    monkeypatch.setattr(inst, "_windows_short_path", lambda path: short)
    assert inst.format_command([spaced, "hook", "stop"], windows=True) == short + " hook stop"
    monkeypatch.setattr(inst, "_windows_short_path", lambda path: None)
    plain = r"C:\tools\agflow.exe"
    assert inst.format_command([plain, "hook", "stop"], windows=True) == plain + " hook stop"
    # spaced path that is also what `agflow` resolves to on PATH -> quote-free bare name
    monkeypatch.setattr(inst.shutil, "which", lambda name: spaced)
    monkeypatch.setattr(inst, "_same_file", lambda a, b: True)
    assert inst.format_command([spaced, "hook", "pre-tool-use"], windows=True) == "agflow hook pre-tool-use"
    assert inst.format_command(["/home/a b/agflow", "hook", "stop"], windows=False) == "'/home/a b/agflow' hook stop"


def test_installed_hooks_use_os_quoting(home, monkeypatch):
    from agflow import install as inst

    monkeypatch.setattr(inst, "agflow_command", lambda: [str(home / "my tools" / "agflow"), ])
    install("global", with_gemini_md=False)
    hooks = json.loads((paths.global_layout().plugins_dir / "agflow" / "hooks.json").read_text())["agflow"]
    cmd = hooks["Stop"][0]["command"]
    assert cmd == hookfile.fail_open(inst.format_command([str(home / "my tools" / "agflow"), "hook", "stop"]))
    mcp = json.loads((paths.global_layout().plugins_dir / "agflow" / "mcp_config.json").read_text())
    assert mcp["mcpServers"]["agflow"]["command"] == str(home / "my tools" / "agflow")  # JSON: no quoting needed


def test_agflow_command_prefers_the_running_executable(tmp_path, monkeypatch):
    from agflow import install as inst

    running = tmp_path / "tool bin" / "agflow"
    running.parent.mkdir()
    running.write_text("#!/bin/sh\n")
    monkeypatch.setattr(inst.sys, "argv", [str(running), "install"])
    monkeypatch.setattr(inst.shutil, "which", lambda name: "/somewhere/else/agflow")  # an older copy on PATH
    assert inst.agflow_command() == [str(running.absolute())]
    monkeypatch.setattr(inst.sys, "argv", ["-c"])  # not launched via the agflow script
    sibling = Path(inst.sys.executable).parent / ("agflow.exe" if os.name == "nt" else "agflow")
    expected = [str(sibling.absolute())] if sibling.is_file() else ["/somewhere/else/agflow"]
    assert inst.agflow_command() == expected


def test_second_copy_is_refused_and_detected_once(home, workspace):
    from agflow.install import InstallError

    install("global", with_gemini_md=False)
    with pytest.raises(InstallError, match="already installed globally"):
        install("workspace", workspace)
    install("workspace", workspace, with_gemini_md=False, allow_duplicate=True)
    issues = validate_inventory(Inventory.discover(workspace))
    codes = [i.code for i in issues]
    assert codes.count("plugin.installed_twice") == 1
    assert not [c for c in codes if c.endswith(".duplicate") or c.endswith(".shadowed")]
    twice = next(i for i in issues if i.code == "plugin.installed_twice")
    assert "plugin:agflow@workspace" in twice.message and "plugin:agflow (" in twice.message
    scopes = {i.scope for i in Inventory.discover(workspace).of(Kind.PLUGIN)}
    assert scopes == {"plugin:agflow", "plugin:agflow@workspace"}


def test_init_skips_workspace_copy_when_global_exists(home, workspace):
    from typer.testing import CliRunner

    from agflow.cli import app

    install("global", with_gemini_md=False)
    r = CliRunner().invoke(app, ["init", "-w", str(workspace)])
    assert r.exit_code == 0 and "installed globally" in r.output
    assert not (workspace / ".agents" / "plugins" / "agflow").exists()
