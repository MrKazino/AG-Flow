"""The plugin manifest says only what Antigravity reads, the doctor shows the daily check's findings, and the
`graph_overview` tool gives an agent the state of every workflow."""

from __future__ import annotations

import json
import time

import pytest
from mcp import Client

from agflow import antigravity_probe as ap
from agflow import compat, learn, paths, update, validate
from agflow import install as inst
from agflow.graph import catalog, engine
from agflow.inventory import Inventory
from agflow.mcp_server import build_server
from tests.conftest import PLUGIN, write

NOW = time.time()


def write_manifest(tmp_path, data) -> object:
    path = tmp_path / "plugin.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


# --- plugin.json ----------------------------------------------------------------------------------


def codes(issues) -> list[str]:
    return [i.code for i in issues]


def test_a_manifest_with_only_documented_fields_is_clean(tmp_path):
    path = write_manifest(tmp_path, {"name": "my-plugin", "displayName": "Mine", "description": "d", "version": "1",
                                     "logo": "assets/logo.svg", "suggestedPrompts": ["a", "b", "c"], "disabled": False})
    assert validate.validate_plugin_manifest(path) == []


def test_fields_antigravity_drops_silently_are_named(tmp_path):
    path = write_manifest(tmp_path, {"name": "p", "version": "1", "description": "d", "author": "me",
                                     "homepage": "https://x.y", "keywords": []})
    [issue] = validate.validate_plugin_manifest(path)
    assert issue.code == "plugin.manifest.field" and issue.level.value == "info"
    assert "author, homepage, keywords" in issue.message and "dropped without a message" in issue.message


def test_suggested_prompts_are_checked(tmp_path):
    many = write_manifest(tmp_path, {"name": "p", "version": "1", "description": "d",
                                     "suggestedPrompts": ["1", "2", "3", "4"]})
    [issue] = validate.validate_plugin_manifest(many)
    assert issue.code == "plugin.manifest.prompts" and issue.level.value == "info" and "first 3" in issue.message
    for bad in ("one prompt", [1, 2], {"a": 1}):
        path = write_manifest(tmp_path, {"name": "p", "version": "1", "description": "d", "suggestedPrompts": bad})
        [issue] = validate.validate_plugin_manifest(path)
        assert issue.code == "plugin.manifest.prompts" and issue.level.value == "warning"


def test_a_manifest_that_is_not_an_object_or_not_json_does_not_crash_the_rule(tmp_path):
    assert codes(validate.validate_plugin_manifest(write_manifest(tmp_path, "[1, 2]")))
    assert codes(validate.validate_plugin_manifest(write_manifest(tmp_path, "{broken")))


def test_the_bundled_manifest_uses_only_fields_antigravity_reads_and_the_baseline_knows_them():
    manifest = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
    assert set(manifest) <= set(validate.MANIFEST_FIELDS)
    documented = ap.Snapshot.from_json(compat.shipped()["verified"]).markers["plugin/SKILL.md"]["table_keys"]
    assert set(manifest) <= set(documented), "the manifest uses a field the verified Antigravity docs do not list"
    assert len(manifest["suggestedPrompts"]) == validate.MANIFEST_PROMPTS_MAX
    assert manifest["displayName"] and manifest["version"] and manifest["description"]


def test_the_rules_own_field_list_is_the_documented_one():
    documented = set(ap.Snapshot.from_json(compat.shipped()["verified"]).markers["plugin/SKILL.md"]["table_keys"])
    assert set(validate.MANIFEST_FIELDS) <= documented


# --- links ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("link, local", [
    ("[x](sidecar://agflow/manager/)", False), ("[x](https://a.b/c)", False), ("[x](ftp://a/b)", False),
    ("[x](mailto:a@b.c)", False), ("[x](#anchor)", False), ("[x](/abs/path)", False),
    ("[x](./references/notes.md)", True), ("[x](references/notes.md#part)", True), ("[x](C:/projects/notes.md)", True),
])
def test_links_with_a_url_scheme_are_not_files_to_find(link, local):
    assert (validate._local_refs(link) != set()) is local


# --- the doctor shows what the daily check found --------------------------------------------------


@pytest.fixture
def inventory(workspace, home):
    inst.install("global", with_gemini_md=False)
    return Inventory.discover(workspace)


def health_codes(inventory) -> dict[str, str]:
    return {i.code: i.level.value for i in validate.validate_inventory(inventory) if i.source == "agflow"}


def test_without_a_cached_check_the_doctor_says_nothing_new(inventory):
    assert health_codes(inventory) == {}


def test_a_cached_breaking_result_is_a_warning_and_drift_is_a_note(inventory):
    compat.save_last(compat.Report(status="breaking"))
    assert health_codes(inventory) == {"compat.breaking": "warning"}
    compat.save_last(compat.Report(status="drift"))
    assert health_codes(inventory) == {"compat.drift": "info"}
    compat.save_last(compat.Report(status="ok"))
    assert health_codes(inventory) == {}


def test_an_available_update_is_a_note_with_its_version(inventory):
    update.save_state({"checked_at": 1.0, "latest": update.ReleaseInfo("99.0.0", "v99.0.0").to_json()})
    issues = [i for i in validate.validate_inventory(inventory) if i.code == "update.available"]
    assert len(issues) == 1 and "99.0.0" in issues[0].message and issues[0].level.value == "info"


def test_a_broken_cache_never_breaks_the_doctor(inventory, monkeypatch):
    from agflow import maintenance

    def boom(*a, **k):
        raise RuntimeError("cache exploded")

    monkeypatch.setattr(maintenance, "health", boom)
    assert validate._health_issues() == []
    assert validate.validate_inventory(inventory) is not None


def test_checking_a_plugin_template_never_shows_machine_state(workspace, home):
    compat.save_last(compat.Report(status="breaking"))
    inv = Inventory.discover(None, include_global=False, include_plugins=False, include_legacy=False,
                             extra_layouts=[paths.plugin_layout(PLUGIN)])
    assert not any(i.code.startswith("compat.") for i in validate.validate_inventory(inv))


# --- the MCP tool ---------------------------------------------------------------------------------


GRAPH = """
id: wf
nodes:
  - {id: a, agent: architect, outputs: ["docs/a.md"]}
  - {id: b, agent: ghost-agent, depends_on: [a]}
"""


def parse(result):
    return result.structured_content or json.loads(result.content[0].text)


async def test_graph_overview_summarises_every_workflow_and_the_panel(workspace, graphs_dir, home):
    inst.install("global", with_gemini_md=False)
    write(graphs_dir / "wf.yaml", GRAPH)
    graph = catalog.resolve("wf", workspace)[0]
    state = engine.new_run(graph, {}, str(workspace), now=NOW, run_id="wf-1")
    state.status = "succeeded"  # type: ignore[assignment]
    learn.record_run(workspace, state, [], now=NOW)
    async with Client(build_server()) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        assert "graph_overview" in names
        got = parse(await c.call_tool("graph_overview", {"workspace": str(workspace)}))
        assert got["ok"] and got["panel"] == {"ready": False, "link": None}
        rows = {w["id"]: w for w in got["workflows"]}
        assert {"bugfix", "feature-delivery", "wf"} <= set(rows)
        assert rows["wf"]["steps"] == 2 and rows["wf"]["problems"]["error"] == 1
        assert rows["wf"]["runs"] == {"recorded": 1, "succeeded": 1, "failed": 0, "success_rate": 1.0}
        assert got["notes"] == []
        paths.antigravity_config_file().write_text(json.dumps({"sidecars": {"agflow/manager": {"enabled": True}}}),
                                                   encoding="utf-8")
        compat.save_last(compat.Report(status="breaking"))
        again = parse(await c.call_tool("graph_overview", {"workspace": str(workspace)}))
        assert again["panel"] == {"ready": True, "link": "[AG-Flow Manager](sidecar://agflow/manager/)"}
        assert again["notes"] == ["Antigravity breaking: run `agflow sync check`"]
        bad = parse(await c.call_tool("graph_overview", {"workspace": "relative/path"}))
        assert bad["ok"] is False and "absolute" in bad["error"]


def test_the_manager_skill_only_offers_the_link_when_the_panel_is_on():
    text = (PLUGIN / "skills" / "workflow-manager" / "SKILL.md").read_text(encoding="utf-8")
    assert "sidecar://agflow/manager/" in text and "**ready**" in text
    assert "never switch a sidecar on yourself" in text and "do not show the link until it is on" in text
