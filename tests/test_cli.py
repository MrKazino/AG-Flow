import json

from typer.testing import CliRunner

from agflow.cli import app
from tests.conftest import BUNDLED_SKILLS

runner = CliRunner()


def invoke(*args):
    return runner.invoke(app, [str(a) for a in args], catch_exceptions=False)


def test_doctor_plugin_strict(plugin_dir):
    r = invoke("doctor", "--path", plugin_dir, "--strict", "--json")
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["counts"]["error"] == 0 and data["items"]["skill"] == BUNDLED_SKILLS


def test_doctor_fails_on_errors(tmp_path):
    bad = tmp_path / "p"
    (bad / "rules").mkdir(parents=True)
    (bad / "plugin.json").write_text('{"name": "p", "version": "1", "description": "d"}')
    (bad / "rules" / "r.md").write_text("no frontmatter")
    assert invoke("doctor", "--path", bad).exit_code == 1


def test_init_install_list_status(workspace):
    assert invoke("init", "-w", workspace, "--profile", "core").exit_code == 0
    assert (workspace / ".agents" / "plugins" / "agflow" / "plugin.json").is_file()
    r = invoke("list", "skill", "-w", workspace, "--json")
    names = {i["name"] for i in json.loads(r.output)}
    assert "tdd" in names and "ci-fix" not in names
    r = invoke("status", "-w", workspace, "--json")
    assert json.loads(r.output)["install"]["workspace"]["installed"] is True


def test_scaffolding_commands(workspace):
    assert invoke("skills", "new", "api-check", "-d", "Check API contracts against the OpenAPI spec. Use when "
                  "endpoints change.", "-w", workspace).exit_code == 0
    assert invoke("agents", "new", "dba", "-d", "Database specialist", "-w", workspace).exit_code == 0
    assert invoke("rules", "new", "py", "--trigger", "glob", "--globs", "**/*.py", "-w", workspace).exit_code == 0
    assert invoke("rules", "new", "bad", "--trigger", "glob", "-w", workspace).exit_code == 1
    r = invoke("doctor", "-w", workspace, "--no-global", "--json")
    assert json.loads(r.output)["counts"]["error"] == 0
    assert invoke("skills", "disable", "api-check", "-w", workspace).exit_code == 0
    assert invoke("skills", "enable", "api-check", "-w", workspace).exit_code == 0


def test_graph_commands(workspace, tmp_path):
    assert "flowchart TD" in invoke("graph", "render", "bugfix", "-w", workspace).output
    r = invoke("graph", "run", "bugfix", "-i", "bug=x", "--backend", "fake", "-w", workspace)
    assert r.exit_code == 0, r.output
    r = invoke("graph", "run", "release", "-i", "version=1.2.3", "--backend", "fake", "-w", workspace)
    assert r.exit_code == 3
    run_id = next(line.split()[2] for line in r.output.splitlines() if line.startswith("▶ run"))
    assert invoke("graph", "approve", run_id, "approve", "-w", workspace).exit_code == 0
    assert invoke("graph", "resume", run_id, "--backend", "fake", "-w", workspace).exit_code == 0
    assert invoke("graph", "compile", "bugfix", "-w", workspace).exit_code == 0
    assert (workspace / ".agents" / "skills" / "bugfix" / "SKILL.md").is_file()
    assert invoke("graph", "run", "bugfix", "--backend", "fake", "-w", workspace).exit_code == 1  # missing input


def test_mcp_commands(workspace):
    assert invoke("mcp", "add", "github", "--scope", "workspace", "-w", workspace).exit_code == 0
    assert invoke("mcp", "disable", "github", "--scope", "workspace", "-w", workspace).exit_code == 0
    r = invoke("mcp", "list", "-w", workspace, "--json")
    assert [i["enabled"] for i in json.loads(r.output) if i["name"] == "github"] == [False]
    assert invoke("mcp", "remove", "github", "--scope", "workspace", "-w", workspace).exit_code == 0
