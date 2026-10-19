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
from tests.conftest import BUNDLED_AGENTS, BUNDLED_SKILLS, write


def codes(issues, level=None):
    return {i.code for i in issues if level is None or i.level == level}


def plugin_inventory(path: Path) -> Inventory:
    return Inventory.discover(None, include_global=False, include_plugins=False, include_legacy=False,
                              extra_layouts=[paths.plugin_layout(path)])


def test_bundled_plugin_is_clean(plugin_dir):
    inv = plugin_inventory(plugin_dir)
    issues = validate_inventory(inv)
    assert [str(i) for i in issues if i.level != Level.INFO] == []
    assert len(inv.of(Kind.SKILL)) == BUNDLED_SKILLS
    assert len(inv.of(Kind.AGENT)) == BUNDLED_AGENTS
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


# --- F27: the reference check and the rule-file reads stay linear -------------------------------------------------


def _reference_ref_exists(skill_dir: Path, ref: str) -> bool:
    """The implementation before F27, kept as the specification of what a reference may point at."""
    suffixes = ("", ".md", ".py", ".txt", ".json", ".yaml", ".yml", ".sh", ".ps1")
    bases = [skill_dir, skill_dir.parent, skill_dir.parent.parent]
    if skill_dir.parent.is_dir():
        bases += [d for d in skill_dir.parent.iterdir() if d.is_dir() and d != skill_dir]
    return any((base / f"{ref}{suffix}").exists() for base in bases for suffix in suffixes)


def _skills_tree(root: Path, count: int) -> list[Path]:
    root.mkdir(parents=True, exist_ok=True)
    (root.parent / "shared-at-grandparent.md").write_text("g", encoding="utf-8")
    (root / "shared-at-parent.md").write_text("p", encoding="utf-8")
    dirs = []
    for i in range(count):
        d = root / f"skill-{i}"
        (d / "references").mkdir(parents=True)
        (d / "references" / f"own-{i}.md").write_text("x", encoding="utf-8")
        (d / "references" / "common.md").write_text("x", encoding="utf-8")
        (d / "scripts").mkdir()
        (d / "scripts" / f"run-{i}.py").write_text("x", encoding="utf-8")
        (d / "SKILL.md").write_text("x", encoding="utf-8")
        dirs.append(d)
    return dirs


REFS = [
    "references/own-1", "references/own-1.md", "references/common.md", "references/common",       # next to the skill
    "references/own-2.md", "scripts/run-3", "skill-2/SKILL.md", "skill-2/references/own-2",       # a sibling's file
    "shared-at-parent.md", "shared-at-parent", "shared-at-grandparent", "../skill-3/SKILL.md",    # up the tree
    "references/missing.md", "scripts/none.py", "skill-99/SKILL.md", "nowhere/at/all.txt",        # not there
    "references", "scripts/", "skill-1", "./references/common.md", "references/../references/common.md",
    "Skill-2/skill.md", "REFERENCES/COMMON.MD",                                                   # case differences
]


@pytest.mark.parametrize("ref", REFS)
def test_ref_exists_answers_exactly_as_before_the_cache(tmp_path, ref):
    from agflow.validate import _FsCache, _ref_exists

    skills = _skills_tree(tmp_path / "plugin" / "skills", 5)
    cache = _FsCache()
    for skill in skills:
        expected = _reference_ref_exists(skill, ref)
        assert _ref_exists(skill, ref, cache) is expected, (skill.name, ref)
        assert _ref_exists(skill, ref) is expected  # without a cache too (call-compatible)


def _count_probes(monkeypatch, skills, refs_of):
    import os

    from agflow.validate import _FsCache, _ref_exists

    real_exists, real_scandir = os.path.exists, os.scandir
    calls = {"exists": 0, "scandir": 0}

    def counting_exists(path):
        calls["exists"] += 1
        return real_exists(path)

    def counting_scandir(path):
        calls["scandir"] += 1
        return real_scandir(path)

    monkeypatch.setattr(os.path, "exists", counting_exists)
    monkeypatch.setattr(os, "scandir", counting_scandir)
    cache = _FsCache()
    for skill in skills:
        for ref in refs_of(skill):
            _ref_exists(skill, ref, cache)
    return calls


def test_references_shared_by_many_skills_are_probed_a_linear_number_of_times(tmp_path, monkeypatch):
    """Before F27 every reference of every skill listed and probed every other skill: ~n**2 * 9 stats per reference."""
    n = 120
    skills = _skills_tree(tmp_path / "plugin" / "skills", n)
    calls = _count_probes(monkeypatch, skills, lambda skill: (
        f"references/own-{skill.name[6:]}.md", "references/common.md", "scripts/gone.py", "references/gone.md"))
    assert calls["scandir"] == 1, "the siblings are listed once for the skills root, not per skill or per reference"
    assert calls["exists"] < 50 * n, calls  # measured 22 * n; the search before F27 made ~ 2200 * n for the same work


def test_a_reference_unique_to_each_skill_costs_one_probe_per_directory_not_nine(tmp_path, monkeypatch):
    n = 120
    skills = _skills_tree(tmp_path / "plugin" / "skills", n)
    calls = _count_probes(monkeypatch, skills, lambda skill: (f"other-{skill.name}/SKILL.md",))
    assert calls["scandir"] == 1
    assert calls["exists"] <= n * (n + 3), calls  # one probe of the first path component per candidate directory


def test_a_rule_file_is_read_at_most_twice_by_discovery_and_validation(tmp_path, monkeypatch):
    plugin = tmp_path / "plugin"
    write(plugin / "plugin.json", '{"name": "p", "version": "1", "description": "d"}')
    write(plugin / "rules" / "always.md", "---\ntrigger: always_on\ndescription: d\n---\n" + "text " * 50)
    write(plugin / "rules" / "glob.md", "---\ntrigger: glob\nglobs: '*.py'\n---\nx\n")
    write(plugin / "skills" / "s" / "SKILL.md",
          "---\nname: s\ndescription: A skill used to count how often files are read during doctor.\n---\n# T\nbody\n")
    reads: dict[Path, int] = {}
    real = Path.read_text

    def counting(self, *args, **kwargs):
        reads[self] = reads.get(self, 0) + 1
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting)
    inv = plugin_inventory(plugin)
    validate_inventory(inv)
    watched = {p: n for p, n in reads.items() if p.suffix == ".md"}
    assert watched and max(watched.values()) <= 2, watched


def test_the_always_on_budget_still_counts_every_always_on_rule(tmp_path):
    plugin = tmp_path / "plugin"
    write(plugin / "plugin.json", '{"name": "p", "version": "1", "description": "d"}')
    for i in range(5):
        write(plugin / "rules" / f"r{i}.md", "---\ntrigger: always_on\ndescription: d\n---\n" + "x" * 5_000)
    issues = validate_inventory(plugin_inventory(plugin))
    assert "context.always_on" in codes(issues, Level.WARNING)  # 25k+ chars of always-on rules
    inv = plugin_inventory(plugin)
    assert sum(i.meta["chars"] for i in inv.of(Kind.RULE)) > 25_000


# --- F24b: input names ------------------------------------------------------------------------------------------------


def graph_with_inputs(*names):
    inputs = "".join(f"  {name}: {{type: string}}\n" for name in names)
    return f"id: g\ntitle: G\ninputs:\n{inputs}nodes:\n  - {{id: a, agent: x}}\n"


def test_input_names_that_break_the_node_id_style_get_a_warning_not_an_error(workspace, graphs_dir):
    write(graphs_dir / "g.yaml", graph_with_inputs("feature", "has_ui", "bug-id", "Feature", "bugID", "1st"))
    found = [i for i in validate_inventory(Inventory.discover(workspace)) if i.code == "graph.input.name"]
    assert sorted(i.message.split("'")[1] for i in found) == ["1st", "Feature", "bugID"]
    assert all(i.level == Level.WARNING and "0.5.0" in i.message for i in found)


def test_a_hyphenated_input_name_is_valid_but_comes_with_a_hint_on_how_to_read_it(workspace, graphs_dir):
    """N4: `bug-id` matches the node-id style, so it is not warned about; but `{{ inputs.bug-id }}` subtracts."""
    write(graphs_dir / "g.yaml", graph_with_inputs("bug-id", "bug_id"))
    issues = validate_inventory(Inventory.discover(workspace))
    hint = [i for i in issues if i.code == "graph.input.hyphen"]
    assert len(hint) == 1 and hint[0].level == Level.INFO and "inputs['bug-id']" in hint[0].message
    assert "graph.input.name" not in codes(issues)


def test_the_graph_still_loads_and_runs_with_such_a_name(workspace, graphs_dir):
    """The warning is the whole change: the break is held back to 0.5.0, so a user's graph keeps working."""
    from agflow.graph.schema import coerce_inputs, load_graph

    write(graphs_dir / "g.yaml", graph_with_inputs("Feature"))
    graph = load_graph(graphs_dir / "g.yaml")
    assert coerce_inputs(graph, {"Feature": "x"}) == {"Feature": "x"}
    assert "graph.invalid" not in codes(validate_inventory(Inventory.discover(workspace)))


def test_no_bundled_graph_has_an_input_name_the_warning_would_flag():
    from agflow.graph.schema import ID_RE, load_graph

    bundled = sorted((Path(__file__).resolve().parents[1] / "plugin" / "graphs").glob("*.yaml"))
    assert bundled
    assert [(g.name, n) for g in bundled for n in load_graph(g).inputs if not ID_RE.match(n)] == []
