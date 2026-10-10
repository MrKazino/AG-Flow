from pathlib import Path

import pytest

from agflow import paths
from agflow.inventory import Inventory
from agflow.models import Kind, Level
from agflow.validate import (
    validate_agent,
    validate_hooks_file,
    validate_inventory,
    validate_mcp_file,
    validate_rule,
    validate_skill,
)
from tests.conftest import write


def codes(issues, level=None):
    return {i.code for i in issues if level is None or i.level == level}


def plugin_inventory(path: Path) -> Inventory:
    return Inventory.discover(None, include_global=False, include_plugins=False, include_legacy=False,
                              extra_layouts=[paths.plugin_layout(path)])


def test_bundled_plugin_is_clean(plugin_dir):
    inv = plugin_inventory(plugin_dir)
    issues = validate_inventory(inv)
    assert [str(i) for i in issues if i.level != Level.INFO] == []
    assert len(inv.of(Kind.SKILL)) == 20
    assert len(inv.of(Kind.AGENT)) == 9
    assert len(inv.of(Kind.GRAPH)) == 5


def test_bundled_gemini_md_under_cap(plugin_dir):
    assert len((plugin_dir / "GEMINI.md").read_text(encoding="utf-8")) < paths.GEMINI_MD_CHAR_LIMIT


def test_rule_trigger_rules(tmp_path):
    assert "frontmatter.missing" in codes(validate_rule(write(tmp_path / "a.md", "# no fm\n")))
    assert "rule.trigger" in codes(validate_rule(write(tmp_path / "b.md", "---\ntrigger: sometimes\n---\nx\n")))
    assert "rule.globs" in codes(validate_rule(write(tmp_path / "c.md", "---\ntrigger: glob\n---\nx\n")))
    assert "rule.description" in codes(validate_rule(write(tmp_path / "d.md", "---\ntrigger: model_decision\n---\nx")))
    ok = write(tmp_path / "e.md", "---\ntrigger: glob\nglobs: '**/*.py'\n---\nx\n")
    assert validate_rule(ok) == []
    big = write(tmp_path / "f.md", "---\ntrigger: always_on\n---\n" + "x" * 12_001)
    assert "rule.too_long" in codes(validate_rule(big))


def test_skill_rules(tmp_path):
    bad = write(tmp_path / "My_Skill" / "SKILL.md", "---\nname: My_Skill\ndescription: short\n---\n")
    c = codes(validate_skill(bad))
    assert {"skill.name.format", "skill.description.short", "skill.body.empty"} <= c
    mismatch = write(tmp_path / "folder" / "SKILL.md",
                     "---\nname: other\ndescription: " + "d" * 60 + "\n---\nbody\n")
    assert "skill.name.folder" in codes(validate_skill(mismatch), Level.WARNING)
    missing_ref = write(tmp_path / "ref" / "SKILL.md",
                        "---\nname: ref\ndescription: " + "d" * 60 + "\n---\nRun `scripts/nope.py`\n")
    assert "skill.ref.missing" in codes(validate_skill(missing_ref))


def test_agent_rules(tmp_path):
    f = write(tmp_path / "a.md", "---\nname: a\ndescription: x\nmodel: ultra\ncommandExecutionPolicy: yolo\n"
                                 "tools: [view_file, teleport]\nagents: [./ghost.md]\n---\nbody\n")
    c = codes(validate_agent(f, known_agents={"a"}))
    assert {"agent.model", "agent.exec_policy", "agent.unreachable", "agent.tools.unknown",
            "agent.tools.invoke", "agent.agents.unknown"} <= c


def test_hooks_file(tmp_path):
    f = write(tmp_path / "hooks.json", '{"hooks": {"BeforeEverything": [{"command": "x"}], '
                                       '"Stop": [{"matcher": "(", "command": "y"}, {}]}}')
    c = codes(validate_hooks_file(f))
    assert {"hooks.event", "hooks.matcher", "hooks.command"} <= c
    assert "hooks.json" in codes(validate_hooks_file(write(tmp_path / "bad.json", "{nope")))


def test_mcp_file(tmp_path):
    f = write(tmp_path / "mcp.json", '{"mcpServers": {"a": {"command": "x", "env": {"API_KEY": "sk-live-123"}},'
                                     '"b": {"command": "x", "serverUrl": "http://y"},'
                                     '"c": {"command": "x", "env": {"API_KEY": "${API_KEY}"}}}}')
    issues = validate_mcp_file(f)
    assert [i.code for i in issues].count("mcp.secret") == 1
    assert "mcp.transport" in codes(issues)


def test_inventory_warnings(workspace, home):
    write(workspace / ".agents" / "workflows" / "ship.md", "---\ndescription: ship it\n---\n1. do it\n")
    write(workspace / ".agent" / "rules" / "old.md", "---\ntrigger: always_on\n---\nx\n")
    write(home / ".gemini" / "GEMINI.md", "x" * 12_500)
    issues = validate_inventory(Inventory.discover(workspace))
    c = codes(issues)
    assert {"workflow.deprecated", "legacy.config", "gemini_md.too_long"} <= c


def test_shadowing_is_info(workspace, home):
    body = "---\nname: dup\ndescription: " + "d" * 50 + "\n---\nbody\n"
    write(workspace / ".agents" / "skills" / "dup" / "SKILL.md", body)
    write(home / ".gemini" / "config" / "skills" / "dup" / "SKILL.md", body)
    inv = Inventory.discover(workspace)
    assert inv.find(Kind.SKILL, "dup").scope == "workspace"
    issues = [i for i in validate_inventory(inv) if i.code == "skill.shadowed"]
    assert issues and issues[0].level == Level.INFO


def test_invalid_graph_reported(workspace, graphs_dir):
    write(graphs_dir / "bad.yaml", "id: bad\nnodes:\n  - {id: a, agent: x, depends_on: [zzz]}\n")
    assert "graph.invalid" in codes(validate_inventory(Inventory.discover(workspace)))


# --- regressions from a real Windows machine with third-party plugins -------------------------------

DESC = "d" * 60


def test_underscore_folder_is_only_info(tmp_path):
    f = write(tmp_path / "chembl_database" / "SKILL.md", f"---\nname: chembl-database\ndescription: {DESC}\n---\nb\n")
    issues = validate_skill(f)
    assert [(i.code, i.level) for i in issues] == [("skill.name.folder", Level.INFO)]


def test_refs_resolved_with_suffix_or_sibling(tmp_path):
    skills = tmp_path / "skills"
    write(skills / "a" / "references" / "Guide.md", "x")
    write(skills / "b" / "scripts" / "helper.py", "x")
    f = write(skills / "a" / "SKILL.md",
              f"---\nname: a\ndescription: {DESC}\n---\nSee `references/Guide` and `scripts/helper.py`.\n")
    assert validate_skill(f) == []
    g = write(skills / "c" / "SKILL.md", f"---\nname: c\ndescription: {DESC}\n---\nRun `scripts/nope.ps1`\n")
    assert [(i.code, i.level) for i in validate_skill(g)] == [("skill.ref.missing", Level.WARNING)]


def test_third_party_plugin_issues_are_attributed_and_not_errors(home, workspace):
    plugin = home / ".gemini" / "config" / "plugins" / "science"
    write(plugin / "plugin.json", '{"name": "science"}')
    write(plugin / "skills" / "pdb_database" / "SKILL.md", f"---\nname: pdb-database\ndescription: {DESC}\n---\nb\n")
    issues = validate_inventory(Inventory.discover(workspace))
    mine = [i for i in issues if i.source == "plugin:science"]
    assert mine and all(i.level == Level.INFO for i in mine)
    from agflow.validate import by_source
    assert by_source(issues)["plugin:science"]["error"] == 0


def test_antigravity_app_data_dir_is_not_legacy(home, workspace):
    write(home / ".gemini" / "antigravity" / "brain" / "x.json", "{}")
    write(home / ".gemini" / "antigravity" / "conversations" / "y.pb", "")
    assert "legacy.config" not in codes(validate_inventory(Inventory.discover(workspace)))
    write(home / ".gemini" / "antigravity" / "skills" / "old" / "SKILL.md",
          f"---\nname: old\ndescription: {DESC}\n---\nb")
    legacy = [i for i in validate_inventory(Inventory.discover(workspace)) if i.code == "legacy.config"]
    assert legacy and "1 skill(s)" in legacy[0].message and legacy[0].source == "legacy"


def test_oversized_gemini_md_is_reported_once(home, workspace):
    write(home / ".gemini" / "GEMINI.md", "x" * 27_012)
    c = [i.code for i in validate_inventory(Inventory.discover(workspace))]
    assert c.count("gemini_md.too_long") == 1 and "context.always_on" not in c
    rules = home / ".gemini" / "config" / "plugins" / "p"
    write(rules / "plugin.json", '{"name": "p", "version": "1", "description": "d"}')
    for n in range(3):  # other always-on rules that are too big on their own still warn
        write(rules / "rules" / f"r{n}.md", "---\ntrigger: always_on\n---\n" + "y" * 11_000)
    assert "context.always_on" in [i.code for i in validate_inventory(Inventory.discover(workspace))]


# --- F9: no validator may crash because a file is missing, unreadable or binary ---------------------


def _make_bad(tmp_path, state):
    p = tmp_path / "f.md"
    if state == "binary":
        p.write_bytes(b"\xff\xfe\x00\x80 not utf-8")
    elif state == "empty":
        p.write_text("", encoding="utf-8")
    elif state == "directory":
        p.mkdir()
    return p  # "missing": nothing created


@pytest.mark.parametrize("state", ["missing", "binary", "directory", "empty"])
@pytest.mark.parametrize("name", ["validate_gemini_md", "validate_rule", "validate_skill", "validate_agent",
                                  "validate_mcp_file", "validate_hooks_file", "validate_plugin_manifest"])
def test_validators_never_crash_on_missing_binary_or_unreadable_files(tmp_path, name, state):
    import agflow.validate as v

    result = getattr(v, name)(_make_bad(tmp_path, state))  # used to raise FileNotFoundError / UnicodeDecodeError
    assert isinstance(result, list)
    if state != "empty":
        assert [i for i in result if i.code == "unreadable"], f"{name} must say it could not read the file"


@pytest.mark.parametrize("name", ["validate_mcp_file", "validate_hooks_file", "validate_plugin_manifest"])
def test_empty_json_files_are_treated_alike(tmp_path, name):
    import agflow.validate as v

    p = tmp_path / "x.json"
    p.write_text("  \n", encoding="utf-8")
    codes_ = {i.code for i in getattr(v, name)(p)}
    assert not codes_ & {"mcp.json", "hooks.json", "plugin.json"}, "an empty file is 'no content', not invalid JSON"


def test_inventory_validation_survives_files_deleted_after_discovery(tmp_path):
    from agflow.inventory import Inventory
    from agflow.validate import validate_inventory

    ws = tmp_path / "ws"
    (ws / ".git").mkdir(parents=True)
    rule = write(ws / ".agents" / "rules" / "r.md", "---\ntrigger: always_on\ndescription: d\n---\nbody\n")
    mcp = write(ws / ".agents" / "mcp_config.json", '{"mcpServers": {"x": {"command": "echo"}}}')
    plugin = ws / ".agents" / "plugins" / "p"
    write(plugin / "plugin.json", '{"name": "p", "version": "1", "description": "d"}')
    gemini = write(plugin / "GEMINI.md", "# hi\n")
    inv = Inventory.discover(ws, include_global=False, include_legacy=False)
    for f in (rule, mcp, gemini):
        f.unlink()  # the race: the file is there at discovery and gone at validation
    issues = validate_inventory(inv)  # must not raise
    assert {"unreadable"} <= {i.code for i in issues}


def test_an_unreadable_gemini_md_is_reported_once(tmp_path):
    from agflow.inventory import Inventory
    from agflow.validate import validate_inventory

    ws = tmp_path / "ws"
    (ws / ".git").mkdir(parents=True)
    plugin = ws / ".agents" / "plugins" / "p"
    write(plugin / "plugin.json", '{"name": "p", "version": "1", "description": "d"}')
    (plugin / "GEMINI.md").write_bytes(b"\xff\xfe\x00\x80")
    issues = [i for i in validate_inventory(Inventory.discover(ws, include_global=False, include_legacy=False))
              if i.path == plugin / "GEMINI.md"]
    assert len(issues) == 1, [str(i) for i in issues]
