"""Migration, Gemini CLI sync, MCP registry, toggles and profiles."""

import json

import pytest

from agflow import frontmatter, mcp_registry, paths, toggles
from agflow.install import install
from agflow.inventory import Inventory
from agflow.migrate import migrate_all, workflow_to_skill
from agflow.models import Kind
from agflow.profiles import ProfileError, resolve_profile
from agflow.sync import sync_gemini_cli
from agflow.validate import validate_skill
from tests.conftest import write


def test_workflow_to_skill_is_valid(tmp_path):
    text = "---\ndescription: Deploy the app to staging\n---\n1. build\n// turbo\n2. deploy\n"
    out = workflow_to_skill(text, "deploy-staging")
    meta, body = frontmatter.parse(out)
    assert meta["name"] == "deploy-staging"
    assert meta["description"].startswith("Deploy the app to staging. Use when the user invokes /deploy-staging")
    assert "// turbo" not in body
    f = write(tmp_path / "deploy-staging" / "SKILL.md", out)
    assert validate_skill(f) == []


def test_migrate_all(workspace, home):
    wf = write(workspace / ".agents" / "workflows" / "Ship It.md", "---\ndescription: ship\n---\nsteps\n")
    write(workspace / ".agent" / "rules" / "style.md", "---\ntrigger: always_on\n---\nx\n")
    write(home / ".gemini" / "antigravity" / "skills" / "old" / "SKILL.md", "---\nname: old\ndescription: d\n---\nb\n")
    write(home / ".gemini" / "antigravity" / "mcp_config.json", '{"mcpServers": {"legacy": {"command": "x"}}}')
    dry = migrate_all(workspace, dry_run=True)
    assert len(dry.actions) == 4 and wf.exists()
    report = migrate_all(workspace)
    assert (workspace / ".agents" / "skills" / "ship-it" / "SKILL.md").is_file()
    assert (workspace / ".agents" / "workflows" / "Ship It.md.bak").is_file()
    assert (workspace / ".agents" / "rules" / "style.md").is_file()
    assert (home / ".gemini" / "config" / "skills" / "old" / "SKILL.md").is_file()
    assert "legacy" in json.loads((home / ".gemini" / "config" / "mcp_config.json").read_text())["mcpServers"]
    assert not report.conflicts
    again = migrate_all(workspace)
    assert not again.actions  # idempotent


def test_mcp_registry(home):
    f = mcp_registry.config_file("global", None)
    mcp_registry.add_server(f, "github")  # from the catalog
    mcp_registry.add_server(f, "local", command="node", args=["srv.js"], env={"TOKEN": "${MY_TOKEN}"})
    mcp_registry.add_server(f, "remote", url="https://mcp.example.com")
    with pytest.raises(mcp_registry.McpError):
        mcp_registry.add_server(f, "local", command="x")
    with pytest.raises(mcp_registry.McpError):
        mcp_registry.add_server(f, "nope")
    mcp_registry.set_enabled(f, "local", False)
    servers = mcp_registry.list_servers(f)
    assert servers["local"]["disabled"] is True and servers["remote"] == {"serverUrl": "https://mcp.example.com"}
    assert mcp_registry.missing_env(servers["local"], {}) == ["MY_TOKEN"]
    mcp_registry.remove_server(f, "remote")
    assert "remote" not in mcp_registry.list_servers(f)
    inv = Inventory.discover(None)
    assert {i.name for i in inv.of(Kind.MCP, enabled=None)} == {"github", "local"}
    assert inv.find(Kind.MCP, "local", enabled=False) is not None


def test_toggle_skill_roundtrip(workspace):
    body = "---\nname: mine\ndescription: " + "d" * 50 + "\n---\nbody\n"
    skill = write(workspace / ".agents" / "skills" / "mine" / "SKILL.md", body)
    parked = toggles.disable(Inventory.discover(workspace), Kind.SKILL, "mine")
    assert not skill.exists() and parked.is_dir()
    inv = Inventory.discover(workspace)
    assert inv.find(Kind.SKILL, "mine") is None
    assert inv.find(Kind.SKILL, "mine", enabled=False) is not None
    with pytest.raises(toggles.ToggleError):
        toggles.disable(inv, Kind.SKILL, "mine")
    toggles.enable(inv, Kind.SKILL, "mine")
    assert skill.read_text() == body


def test_profiles_resolve_and_apply(workspace):
    core = resolve_profile("core")
    secure = resolve_profile("secure")
    assert set(core["skills"]) < set(secure["skills"]) and "security-auditor" in secure["agents"]
    assert len(resolve_profile("full")["skills"]) == 20
    with pytest.raises(ProfileError):
        resolve_profile("nope")
    write(paths.workspace_state_dir(workspace) / "profiles.yaml",
          "profiles:\n  mini:\n    skills: [tdd]\n    agents: [implementer]\n    mcp: [github]\n")
    install("global", with_gemini_md=False)
    install("workspace", workspace, with_gemini_md=False, allow_duplicate=True)  # deliberately both
    f = paths.workspace_layout(workspace).mcp_file
    mcp_registry.add_server(f, "github")
    mcp_registry.add_server(f, "memory")
    actions = toggles.apply_profile(workspace, resolve_profile("mini", None, workspace))
    def ws_names(kind):
        items = Inventory.discover(workspace, include_global=False).of(kind)
        return {i.name for i in items if workspace.resolve() in i.path.resolve().parents}

    assert ws_names(Kind.SKILL) == {"tdd"}
    assert ws_names(Kind.AGENT) == {"implementer", "orchestrator"}
    assert mcp_registry.list_servers(f)["memory"]["disabled"] is True
    global_skills = [i for i in Inventory.discover(None).of(Kind.SKILL) if i.scope == "plugin:agflow"]
    assert len(global_skills) == 20  # the global plugin is untouched
    assert any("disable mcp memory" in a for a in actions)
    toggles.apply_profile(workspace, resolve_profile("full", None, workspace))
    assert len(ws_names(Kind.SKILL)) == 20


def test_sync_gemini_cli(home):
    install("global", with_gemini_md=False)
    write(home / ".gemini" / "skills" / "tdd" / "SKILL.md", "user-owned")
    mcp_registry.add_server(mcp_registry.config_file("global", None), "remote", url="https://x")
    report = sync_gemini_cli(None)
    settings = json.loads((home / ".gemini" / "settings.json").read_text())
    assert settings["mcpServers"]["remote"] == {"httpUrl": "https://x"}
    assert "agflow" in settings["mcpServers"]
    assert (home / ".gemini" / "skills" / "plan-spec" / "SKILL.md").is_file()
    assert (home / ".gemini" / "skills" / "tdd" / "SKILL.md").read_text() == "user-owned"
    assert any("tdd" in s for s in report.skipped)


# --- F5: one corrupt agflow-owned file must never take discovery down -----------------------------------------


def _write_disabled_index(workspace, body: str | bytes):
    f = paths.workspace_state_dir(workspace) / "disabled" / "index.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(body, bytes):
        f.write_bytes(body)
    else:
        f.write_text(body, encoding="utf-8")
    return f


@pytest.mark.parametrize("body", [
    pytest.param("{not json", id="invalid-json"),
    pytest.param(json.dumps({"kind": "skill"}), id="not-a-list"),
    pytest.param(json.dumps(["just a string"]), id="entry-not-an-object"),
    pytest.param(json.dumps([{"kind": "bogus", "name": "x", "parked_path": ".", "original_path": "o"}]),
                 id="unknown-kind"),
    pytest.param(json.dumps([{"kind": "skill"}]), id="missing-key"),
    pytest.param(b"\xff\xfe\x00garbage", id="not-utf8"),
])
def test_discover_survives_corrupt_disabled_index(workspace, body):
    index = _write_disabled_index(workspace, body)
    inv = Inventory.discover(workspace, include_global=False, include_legacy=False)  # must not raise
    assert any(path == index for path, _ in inv.load_errors)


def test_valid_entries_next_to_a_bad_one_still_load(workspace):
    parked = workspace / ".agents" / "agflow" / "disabled" / "skills" / "workspace" / "kept"
    write(parked / "SKILL.md", "---\nname: kept\ndescription: " + "d" * 50 + "\n---\nbody\n")
    index = _write_disabled_index(workspace, json.dumps([
        {"kind": "bogus", "name": "bad", "parked_path": str(parked), "original_path": "o"},
        {"kind": "skill", "name": "kept", "scope": "workspace", "parked_path": str(parked),
         "original_path": str(workspace / ".agents" / "skills" / "kept")},
    ]))
    inv = Inventory.discover(workspace, include_global=False, include_legacy=False)
    assert inv.find(Kind.SKILL, "kept", enabled=False) is not None
    errors = [msg for path, msg in inv.load_errors if path == index]
    assert len(errors) == 1 and "entry #0" in errors[0]


def test_corrupt_disabled_index_is_reported_by_doctor(workspace):
    from agflow.validate import validate_inventory

    index = _write_disabled_index(workspace, "{not json")
    issues = validate_inventory(Inventory.discover(workspace, include_global=False, include_legacy=False))
    assert [i for i in issues if i.code == "load" and i.path == index]


def test_discover_survives_corrupt_plugin_manifest(workspace):
    plugin = workspace / ".agents" / "plugins" / "broken"
    write(plugin / "plugin.json", "{broken")
    inv = Inventory.discover(workspace, include_global=False, include_legacy=False)
    assert any(path == plugin / "plugin.json" for path, _ in inv.load_errors)


@pytest.mark.parametrize("name", ["GEMINI.md", "mcp_config.json", "hooks.json"])
def test_discover_survives_non_utf8_plugin_files(workspace, name):
    plugin = workspace / ".agents" / "plugins" / "binary"
    write(plugin / "plugin.json", json.dumps({"name": "binary", "version": "1", "description": "d"}))
    (plugin / name).write_bytes(b"\xff\xfe\x00\x80 not utf-8")
    inv = Inventory.discover(workspace, include_global=False, include_legacy=False)
    assert any(path == plugin / name for path, _ in inv.load_errors)
