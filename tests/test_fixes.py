import json
from pathlib import Path

import pytest

from agflow import fixes, frontmatter, paths
from agflow.install import install
from agflow.inventory import Inventory
from agflow.validate import validate_inventory
from tests.conftest import write


def codes(workspace: Path | None) -> list[str]:
    return [i.code for i in validate_inventory(Inventory.discover(workspace)) if i.level.value != "info"]


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and ".agflow-backups" not in p.parts and p.name != "fix-history.json"}


def fix_for(plan: fixes.FixPlan, code: str) -> fixes.Fix:
    found = [f for f in plan.fixes if f.code == code]
    assert found, f"no fix for {code}: {[f.code for f in plan.fixes]} / manual {[m.code for m in plan.manual]}"
    return found[0]


def test_installed_twice_keep_global_then_undo(home, workspace):
    install("global")
    install("workspace", workspace, allow_duplicate=True)
    assert codes(workspace).count("plugin.installed_twice") == 1
    before = snapshot(home) | snapshot(workspace)

    plan = fixes.build_plan(workspace)
    fix = fix_for(plan, "plugin.installed_twice")
    assert fix.tier == "review" and fix.default == "keep-global"
    assert [o.id for o in fix.options] == ["keep-global", "keep-workspace"]
    assert fixes.select(plan) == {f.id: None for f in plan.fixes if f.tier == "safe"}  # review not by default

    result = fixes.apply(workspace, {fix.id: None})
    assert not result.failed and result.applied[0]["id"] == fix.id
    assert not (workspace / ".agents" / "plugins" / "agflow").exists()
    assert (paths.global_layout().plugins_dir / "agflow" / "plugin.json").is_file()
    assert "plugin.installed_twice" not in codes(workspace)
    assert result.after.get("warning", 0) < result.before.get("warning", 0)

    undone = fixes.undo_last()
    assert undone["fixes"] and (snapshot(home) | snapshot(workspace)) == before


def test_installed_twice_keep_workspace(home, workspace):
    install("global")
    install("workspace", workspace, allow_duplicate=True)
    fix = fix_for(fixes.build_plan(workspace), "plugin.installed_twice")
    fixes.apply(workspace, {fix.id: "keep-workspace"})
    assert not (paths.global_layout().plugins_dir / "agflow").exists()
    assert (workspace / ".agents" / "plugins" / "agflow" / "plugin.json").is_file()
    assert "plugin.installed_twice" not in codes(workspace)


def _big_gemini_md() -> str:
    parts = ["# My rules\n\nAlways answer briefly.\n\n"]
    for n, topic in enumerate(["Python style", "Frontend work", "Database migrations", "Release process"]):
        parts.append(f"## {topic}\n\n" + f"- {topic} rule {n}: keep it tidy and tested.\n" * 140 + "\n")
    parts.append("<!-- hermes:begin -->\n## Hermes memory\n" + "hermes note\n" * 200 + "<!-- hermes:end -->\n")
    parts.append("## Code sample\n\n```\n# not a heading\nprint('x')\n```\n")
    return "".join(parts)


def test_gemini_md_moves_sections_and_keeps_foreign_blocks(home, workspace):
    install("global")
    gm = paths.global_layout().gemini_md
    text = gm.read_text()
    user = _big_gemini_md()
    gm.write_bytes((user + text).replace("\n", "\r\n").encode())  # Windows line endings must survive
    original = gm.read_bytes()
    assert len(original.decode()) > paths.GEMINI_MD_CHAR_LIMIT
    assert "gemini_md.too_long" in codes(workspace)

    plan = fixes.build_plan(workspace)
    fix = fix_for(plan, "gemini_md.too_long")
    assert fix.tier == "review" and "agflow's own block" in fix.options[0].changes[0]
    result = fixes.apply(workspace, {fix.id: None})
    assert not result.failed, result.failed

    new = gm.read_bytes()
    assert b"\r\n" in new and b"\n" not in new.replace(b"\r\n", b"")
    new_text = new.decode()
    assert len(new_text) <= paths.GEMINI_MD_CHAR_LIMIT
    assert "<!-- hermes:begin -->" in new_text and new_text.count("hermes note") == 200  # untouched
    assert "agflow:begin" not in new_text and "Always answer briefly." in new_text
    assert "Situational rules moved to on-demand skills" in new_text
    moved = sorted(paths.global_layout().skills_dir.glob("gemini-md-*/SKILL.md"))
    assert moved
    for skill in moved:
        meta, body = frontmatter.parse(skill.read_text())
        assert meta["name"] == skill.parent.name and "Use whenever" in meta["description"]
        assert body.count("rule") > 10
    rule = paths.global_layout().plugins_dir / "agflow" / "rules" / "agflow-master.md"
    assert rule.is_file()  # agflow keeps its master prompt through the plugin rule
    remaining = codes(workspace)
    assert "gemini_md.too_long" not in remaining

    fixes.undo_last()
    assert gm.read_bytes() == original
    assert not list(paths.global_layout().skills_dir.glob("gemini-md-*"))


def test_gemini_md_refuses_when_file_changed(home, workspace):
    gm = write(paths.global_layout().gemini_md, _big_gemini_md())
    plan = fixes.build_plan(workspace)
    fix = fix_for(plan, "gemini_md.too_long")
    gm.write_text(gm.read_text() + "\nnew line\n")
    with pytest.raises(fixes.FixError):
        fix.options[0].apply(fixes.Txn())


def test_gemini_md_only_foreign_blocks_is_manual(home, workspace):
    write(paths.global_layout().gemini_md, "<!-- other:begin -->\n" + "x\n" * 7000 + "<!-- other:end -->\n")
    plan = fixes.build_plan(workspace)
    assert not [f for f in plan.fixes if f.code == "gemini_md.too_long"]
    manual = [m for m in plan.manual if m.code == "gemini_md.too_long"]
    assert manual and any("other tools" in s for s in manual[0].steps)


def test_skill_folder_options(home, workspace):
    skill = write(workspace / ".agents" / "skills" / "graphify-main" / "SKILL.md",
                  "---\nname: graphify\ndescription: Build knowledge graphs from a codebase. Use when mapping "
                  "a repo.\n---\n# Graphify\nSteps.\n")
    plan = fixes.build_plan(workspace)
    fix = fix_for(plan, "skill.name.folder")
    assert [o.id for o in fix.options] == ["rename-folder", "set-name"]
    fixes.apply(workspace, {fix.id: "rename-folder"})
    assert (workspace / ".agents" / "skills" / "graphify" / "SKILL.md").is_file() and not skill.exists()
    assert "skill.name.folder" not in codes(workspace)
    fixes.undo_last()
    assert skill.is_file() and not (workspace / ".agents" / "skills" / "graphify").exists()

    fix = fix_for(fixes.build_plan(workspace), "skill.name.folder")
    fixes.apply(workspace, {fix.id: "set-name"})
    assert frontmatter.parse(skill.read_text())[0]["name"] == "graphify-main"
    assert "skill.name.folder" not in codes(workspace)


def test_rule_without_frontmatter_and_bad_trigger(home, workspace):
    plain = write(workspace / ".agents" / "rules" / "testing.md", "# Testing\n\nRun the tests before committing.\n")
    typo = write(workspace / ".agents" / "rules" / "style.md", "---\ntrigger: always-on\n---\nUse ruff.\n")
    plan = fixes.build_plan(workspace)
    missing = next(f for f in plan.fixes if f.path == str(plain))
    trig = next(f for f in plan.fixes if f.path == str(typo))
    assert missing.code == "frontmatter.missing" and missing.default == "model_decision"
    assert trig.code == "rule.trigger" and trig.default == "always_on"  # the typo's intent comes first
    result = fixes.apply(workspace, fixes.select(plan, include_review=True))
    assert not result.failed and not result.unresolved
    meta, body = frontmatter.parse(plain.read_text())
    assert meta["trigger"] == "model_decision" and meta["description"].startswith("Run the tests")
    assert body.startswith("# Testing")
    assert frontmatter.parse(typo.read_text())[0]["trigger"] == "always_on"
    assert codes(workspace) == []


def test_workflow_conversion_is_safe_and_undoable(home, workspace):
    wf = write(workspace / ".agents" / "workflows" / "Deploy App.md",
               "---\ndescription: Deploy the app to staging\n---\n1. Build\n2. Ship\n")
    plan = fixes.build_plan(workspace)
    fix = fix_for(plan, "workflow.deprecated")
    assert fix.tier == "safe" and fixes.select(plan) == {fix.id: None}
    fixes.apply(workspace, fixes.select(plan))
    skill = workspace / ".agents" / "skills" / "deploy-app" / "SKILL.md"
    assert skill.is_file() and not wf.exists() and wf.with_name("Deploy App.md.bak").is_file()
    assert "workflow.deprecated" not in codes(workspace)
    fixes.undo_last()
    assert wf.is_file() and not (workspace / ".agents" / "skills").exists()


def test_third_party_plugin_is_never_touched(home, workspace):
    science = paths.global_layout().plugins_dir / "science"
    write(science / "plugin.json", '{"name": "science", "version": "1.0.0", "description": "Science tools"}')
    write(science / "skills" / "uniprot" / "SKILL.md",
          "---\nname: uniprot\ndescription: Query UniProt for protein data. Use for protein lookups.\n---\n"
          "See [the API notes](references/api.md).\n")
    write(science / "skills" / "badname" / "SKILL.md", "no frontmatter here\n")
    before = snapshot(science)
    plan = fixes.build_plan(workspace)
    assert not [f for f in plan.fixes if f.source.startswith("plugin:science")]
    manual = [m for m in plan.manual if m.source == "plugin:science"]
    assert manual and all("publisher" in m.steps[0] for m in manual)
    fixes.apply(workspace, fixes.select(plan, include_review=True))
    assert snapshot(science) == before


def test_broken_agflow_file_is_reinstalled(home, workspace):
    install("global")
    tdd = paths.global_layout().plugins_dir / "agflow" / "skills" / "tdd" / "SKILL.md"
    good = tdd.read_bytes()
    tdd.write_text("broken\n")
    plan = fixes.build_plan(workspace)
    fix = next(f for f in plan.fixes if f.path == str(tdd))
    assert fix.tier == "safe"
    result = fixes.apply(workspace, fixes.select(plan))
    assert not result.failed and tdd.read_bytes() == good
    assert codes(workspace) == []


def test_agent_fixes(home, workspace):
    agent = write(workspace / ".agents" / "agents" / "reviewer.md",
                  "---\nname: reviewer\ndescription: Reviews diffs for bugs. Use after changes.\nmodel: gpt-4\n"
                  "subagent: 'yes'\n---\nReview carefully.\n")
    plan = fixes.build_plan(workspace)
    model = fix_for(plan, "agent.model")
    flag = fix_for(plan, "agent.flag")
    assert model.default == "inherit" and flag.tier == "safe"
    fixes.apply(workspace, {model.id: None, flag.id: None})
    meta = frontmatter.parse(agent.read_text())[0]
    assert meta["model"] == "inherit" and meta["subagent"] is True


def test_selection_errors_are_reported_not_raised(home, workspace):
    write(workspace / ".agents" / "rules" / "r.md", "---\ntrigger: sometimes\n---\nBody.\n")
    plan = fixes.build_plan(workspace)
    fix = fix_for(plan, "rule.trigger")
    result = fixes.apply(workspace, {fix.id: "nope", "missing-id": None})
    assert result.failed and "no option 'nope'" in result.failed[0]["error"]
    assert result.skipped == [{"id": "missing-id", "reason": "no longer applicable (already fixed or changed)"}]
    assert result.batch is None


def test_history_and_undo_order(home, workspace):
    r1 = write(workspace / ".agents" / "rules" / "a.md", "Alpha rule.\n")
    fixes.apply(workspace, fixes.select(fixes.build_plan(workspace), include_review=True))
    r2 = write(workspace / ".agents" / "rules" / "b.md", "Beta rule.\n")
    fixes.apply(workspace, fixes.select(fixes.build_plan(workspace), include_review=True))
    hist = fixes.history()
    assert len(hist) == 2 and "b.md" not in str(hist[1]["ops"])
    fixes.undo_last()
    assert r2.read_text() == "Beta rule.\n" and r1.read_text() != "Alpha rule.\n"
    fixes.undo_last()
    assert r1.read_text() == "Alpha rule.\n"
    with pytest.raises(fixes.FixError):
        fixes.undo_last()


def test_plan_to_dict_is_json_ready(home, workspace):
    import json

    write(workspace / ".agents" / "rules" / "a.md", "Alpha rule.\n")
    data = fixes.build_plan(workspace).to_dict()
    json.dumps(data)
    assert data["counts"]["error"] == 1 and data["fixes"][0]["options"]


# --- CLI ----------------------------------------------------------------------------------------------------


def _cli(*args, input=None):
    from typer.testing import CliRunner

    from agflow.cli import app

    return CliRunner().invoke(app, [str(a) for a in args], input=input, catch_exceptions=False)


def test_cli_preview_changes_nothing_then_apply_and_undo(home, workspace):
    rule = write(workspace / ".agents" / "rules" / "a.md", "Alpha rule.\n")
    wf = write(workspace / ".agents" / "workflows" / "ship.md", "---\ndescription: Ship it\n---\n1. Ship\n")
    r = _cli("fix", "-w", workspace)
    assert r.exit_code == 0 and "Safe fixes (1)" in r.output and "Needs your choice (1)" in r.output
    assert rule.read_text() == "Alpha rule.\n" and wf.is_file()

    r = _cli("fix", "-w", workspace, "--apply", "--yes")  # safe only
    assert r.exit_code == 0 and "fixed" in r.output and not wf.exists()
    assert rule.read_text() == "Alpha rule.\n"

    fid = fixes.build_plan(workspace).fixes[0].id
    r = _cli("fix", "-w", workspace, "--only", fid, "--option", f"{fid}=always_on", "--yes", "--json")
    data = json.loads(r.output)
    assert data["applied"][0]["id"] == fid and data["after"]["error"] == 0
    assert frontmatter.parse(rule.read_text())[0]["trigger"] == "always_on"

    hist = json.loads(_cli("fix", "--history", "--json").output)
    assert len(hist) == 2
    assert _cli("fix", "--undo", "--yes").exit_code == 0
    assert rule.read_text() == "Alpha rule.\n"
    assert _cli("fix", "--undo", "--yes").exit_code == 0
    assert wf.is_file()
    assert _cli("fix", "--undo", "--yes").exit_code == 1  # nothing left


def test_cli_rejects_unknown_ids_and_options(home, workspace):
    write(workspace / ".agents" / "rules" / "a.md", "Alpha rule.\n")
    assert _cli("fix", "-w", workspace, "--only", "nope").exit_code == 1
    fid = fixes.build_plan(workspace).fixes[0].id
    assert _cli("fix", "-w", workspace, "--option", f"{fid}=bogus").exit_code == 1
    assert _cli("fix", "-w", workspace, "--option", "novalue").exit_code == 1


def test_doctor_fix_asks_for_each_choice(home, workspace):
    rule = write(workspace / ".agents" / "rules" / "a.md", "Alpha rule.\n")
    skill = write(workspace / ".agents" / "skills" / "tool-x" / "SKILL.md",
                  "---\nname: toolx\ndescription: Does tool x things for the repo. Use when x.\n---\nBody\n")
    wf = write(workspace / ".agents" / "workflows" / "ship.md", "---\ndescription: Ship it\n---\n1. Ship\n")
    assert _cli("doctor", "-w", workspace, "--no-global").exit_code == 1
    # safe: yes · rule: option 2 (always_on) · skill folder: skip
    r = _cli("doctor", "-w", workspace, "--fix", input="y\n2\ns\n")
    assert r.exit_code == 0, r.output
    assert not wf.exists() and frontmatter.parse(rule.read_text())[0]["trigger"] == "always_on"
    assert skill.is_file() and "errors 1 → 0" in r.output


def test_doctor_fix_yes_without_terminal_skips_choices(home, workspace):
    rule = write(workspace / ".agents" / "rules" / "a.md", "Alpha rule.\n")
    wf = write(workspace / ".agents" / "workflows" / "ship.md", "---\ndescription: Ship it\n---\n1. Ship\n")
    r = _cli("doctor", "-w", workspace, "--fix", "--yes")
    assert not wf.exists() and rule.read_text() == "Alpha rule.\n"
    assert "skipped (needs a choice)" in r.output and r.exit_code == 1  # the rule error remains
