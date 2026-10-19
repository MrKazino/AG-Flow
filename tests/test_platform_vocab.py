"""The Antigravity vocabulary lives in one place (F32): packaged table + optional, validated override."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from agflow import paths, platform_vocab
from agflow.inventory import Inventory
from agflow.validate import validate_agent, validate_inventory
from tests.conftest import write

# The constants as agflow 0.2.0 hard-coded them. The built-in table must reproduce them exactly: the refactor moves
# the names, it must not change which ones are known.
OLD_TOOLS = {
    "list_dir", "view_file", "view_file_outline", "view_code_item", "grep_search", "find_by_name",
    "codebase_search", "write_to_file", "replace_file_content", "multi_replace_file_content",
    "run_command", "command_status", "send_command_input", "read_terminal", "browser_subagent",
    "read_url_content", "search_web", "generate_image", "invoke_subagent", "define_subagent",
    "ask_question", "notify_user", "task_boundary", "list_resources", "read_resource",
}
OLD_TOOL_MAP = {
    "view_file": "view_file", "view_file_outline": "view_file", "view_code_item": "view_file",
    "list_dir": "list_directory", "grep_search": "search_directory", "codebase_search": "search_directory",
    "find_by_name": "find_file", "write_to_file": "create_file", "replace_file_content": "edit_file",
    "multi_replace_file_content": "edit_file", "run_command": "run_command", "command_status": "run_command",
    "send_command_input": "run_command", "read_terminal": "run_command", "search_web": "search_web",
    "read_url_content": "read_url_content", "invoke_subagent": "start_subagent",
    "define_subagent": "start_subagent", "generate_image": "generate_image",
}


@pytest.fixture(autouse=True)
def fresh_cache():
    platform_vocab._build.cache_clear()
    yield
    platform_vocab._build.cache_clear()


def override(home: Path, body: object, *, workspace: Path | None = None) -> Path:
    base = paths.workspace_state_dir(workspace) if workspace else paths.global_state_dir()
    return write(base / "vocab.json", body if isinstance(body, str) else json.dumps(body))


# --- the built-in table -----------------------------------------------------------------------------------------------


def test_the_builtin_table_reproduces_the_constants_agflow_shipped_with():
    v = platform_vocab.builtin()
    assert set(v.agent_tools) == OLD_TOOLS and len(v.agent_tools) == 25
    assert v.sdk_tool_map == OLD_TOOL_MAP
    assert v.agent_models == ("inherit", "pro", "flash")  # order matters: it is the order fixes offer them in
    assert set(v.exec_policies) == {"off", "auto", "eager", "sandbox"}
    assert set(v.rule_triggers) == {"always_on", "model_decision", "glob", "manual"}
    assert set(v.hook_events) == {"PreToolUse", "PostToolUse", "PreInvocation", "PostInvocation", "Stop"}
    assert v.workflows_retired_on == date(2026, 11, 1)
    assert v.payload_keys["tool_name"] == ("tool_name", "toolName", "tool", "name")
    assert v.payload_keys["command"] == ("CommandLine", "command_line", "commandLine", "command", "cmd")
    assert v.version and v.describe() == f"vocab v{v.version}"


def test_every_mapped_tool_is_a_known_tool_and_roles_exist():
    v = platform_vocab.builtin()
    assert set(v.sdk_tool_map) <= set(v.agent_tools)
    assert {v.command_tool, v.subagent_tool} <= set(v.agent_tools)
    assert set(v.default_agent_tools) <= set(v.agent_tools)
    assert set(v.command_tools) | set(v.file_tools) <= set(v.agent_tools)


def test_other_modules_derive_from_the_table_instead_of_restating_it():
    from agflow import validate
    from agflow.runner import backends

    v = platform_vocab.builtin()
    assert set(v.agent_tools) == validate.KNOWN_AGENT_TOOLS and set(v.agent_models) == validate.AGENT_MODELS
    assert set(v.exec_policies) == validate.AGENT_EXEC_POLICIES and set(v.hook_events) == validate.HOOK_EVENTS
    assert set(v.rule_triggers) == validate.RULE_TRIGGERS
    assert v.sdk_tool_map == backends.TOOL_MAP


def test_the_data_file_ships_inside_the_package():
    assert platform_vocab.DATA_FILE.is_file() and platform_vocab.DATA_FILE.parent.name == "data"
    assert json.loads(platform_vocab.DATA_FILE.read_text(encoding="utf-8"))["vocab_version"]


# --- overrides ----------------------------------------------


def test_without_an_override_current_is_the_builtin_table(home):
    assert platform_vocab.current() is platform_vocab.builtin()


def test_a_global_override_can_add_and_remove_names(home):
    override(home, {"vocab_version": "mine-1",
                    "agent_tools": {"add": ["new_tool"], "remove": ["define_subagent"]},
                    "sdk_tool_map": {"set": {"new_tool": "view_file"}, "remove": ["define_subagent"]},
                    "agent_models": {"add": ["ultra"]},
                    "hook_payload_keys": {"tool_name": {"add": ["toolId"]}},
                    "workflows_retired_on": "2027-01-01"})
    v = platform_vocab.current()
    assert "new_tool" in v.agent_tools and "define_subagent" not in v.agent_tools
    assert v.sdk_tool_map["new_tool"] == "view_file" and "define_subagent" not in v.sdk_tool_map
    assert v.agent_models[-1] == "ultra" and v.payload_keys["tool_name"][-1] == "toolId"
    assert v.workflows_retired_on == date(2027, 1, 1)
    assert v.describe().endswith("+ override mine-1") and v.problems == ()
    assert "new_tool" not in platform_vocab.builtin().agent_tools  # the built-in table itself is never modified


def test_the_workspace_override_applies_after_the_global_one(home, workspace):
    override(home, {"agent_tools": {"add": ["from_global"]}})
    override(home, {"agent_tools": {"add": ["from_workspace"], "remove": ["from_global"]}}, workspace=workspace)
    v = platform_vocab.current(workspace)
    assert "from_workspace" in v.agent_tools and "from_global" not in v.agent_tools
    assert "from_global" in platform_vocab.current().agent_tools  # outside the workspace only the global one applies


def test_an_override_edit_is_picked_up_without_a_restart(home):
    f = override(home, {"agent_tools": {"add": ["first"]}})
    assert "first" in platform_vocab.current().agent_tools
    f.write_text(json.dumps({"agent_tools": {"add": ["second"]}, "vocab_version": "longer-to-change-the-size"}),
                 encoding="utf-8")
    assert "second" in platform_vocab.current().agent_tools and "first" not in platform_vocab.current().agent_tools


@pytest.mark.parametrize("body", [
    "{not json",
    "[]",
    {"surprise": 1},
    {"agent_tools": ["just", "a", "list"]},
    {"agent_tools": {"add": "not-a-list"}},
    {"agent_tools": {"add": [""]}},
    {"agent_tools": {"plus": ["x"]}},
    {"sdk_tool_map": {"set": {"a": 1}}},
    {"hook_payload_keys": {"nonsense_group": {"add": ["x"]}}},
    {"workflows_retired_on": "next spring"},
    {"vocab_version": 7},
])
def test_an_invalid_override_is_ignored_as_a_whole_and_reported(home, body):
    f = override(home, body)
    v = platform_vocab.current()
    assert v.agent_tools == platform_vocab.builtin().agent_tools and v.overrides == ()
    assert [p for p, _ in v.problems] == [f] and v.problems[0][1]


def test_a_half_valid_override_is_not_half_applied(home):
    override(home, {"agent_tools": {"add": ["good_tool"]}, "workflows_retired_on": "not a date"})
    v = platform_vocab.current()
    assert "good_tool" not in v.agent_tools and v.problems


# --- doctor ----------------------------------------------


AGENT = ("---\nname: a\ndescription: An agent used to test the vocabulary.\nsubagent: true\n"
         "tools:\n  - {tool}\n---\nbody\n")


def test_unknown_tool_names_are_reported_as_unknown_to_this_version(tmp_path):
    f = write(tmp_path / "a.md", AGENT.format(tool="brand_new_tool"))
    info = [i for i in validate_agent(f) if i.code == "agent.tools.unknown"]
    assert info and info[0].level.value == "info"
    assert "unknown to this agflow version" in info[0].message
    assert f"vocab v{platform_vocab.builtin().version}" in info[0].message and "brand_new_tool" in info[0].message


def test_an_override_makes_a_new_tool_known(home, tmp_path):
    override(home, {"agent_tools": {"add": ["brand_new_tool"]}})
    f = write(tmp_path / "a.md", AGENT.format(tool="brand_new_tool"))
    assert not [i for i in validate_agent(f) if i.code == "agent.tools.unknown"]


def test_doctor_reports_an_invalid_override_and_an_applied_one(home, workspace):
    bad = override(home, "{broken")
    issues = validate_inventory(Inventory.discover(workspace))
    found = [i for i in issues if i.code == "vocab.override.invalid"]
    assert found and found[0].level.value == "warning" and found[0].path == bad
    bad.unlink()
    override(home, {"vocab_version": "mine-2", "agent_tools": {"add": ["x_tool"]}})
    applied = [i for i in validate_inventory(Inventory.discover(workspace)) if i.code == "vocab.override"]
    assert applied and applied[0].level.value == "info" and "mine-2" in applied[0].message


def test_doctor_on_a_plugin_directory_ignores_the_users_override(home, plugin_dir):
    """`doctor --path` must be deterministic (CI): the user's own override must not leak into it."""
    override(home, {"agent_tools": {"remove": ["view_file"]}})
    inv = Inventory.discover(None, include_global=False, include_plugins=False, include_legacy=False,
                             extra_layouts=[paths.plugin_layout(plugin_dir)])
    assert not [i for i in validate_inventory(inv) if i.code.startswith("vocab.")]


def test_the_workflow_deprecation_date_comes_from_the_table(home, workspace):
    write(workspace / ".agents" / "workflows" / "ship.md", "---\ndescription: Ship\n---\nsteps\n")
    msg = next(i.message for i in validate_inventory(Inventory.discover(workspace, include_global=False))
               if i.code == "workflow.deprecated")
    assert "2026-11-01" in msg and "retires" in msg
    override(home, {"workflows_retired_on": "2020-01-01"})
    msg = next(i.message for i in validate_inventory(Inventory.discover(workspace))
               if i.code == "workflow.deprecated")
    assert "2020-01-01" in msg and "retired" in msg and "retires" not in msg


def test_enum_errors_name_the_vocabulary_so_a_new_value_can_be_added(tmp_path):
    f = write(tmp_path / "a.md", "---\nname: a\ndescription: An agent used to test the vocabulary.\nsubagent: true\n"
                                 "model: ultra\n---\nbody\n")
    msg = next(i.message for i in validate_agent(f) if i.code == "agent.model")
    assert "ultra" in msg and "vocab v" in msg and "vocab.json" in msg


# --- the SDK backend ----------------------------------------------


def test_model_tiers_come_from_the_table(monkeypatch, home):
    from agflow.runner.backends import model_for_tier

    monkeypatch.delenv("AGFLOW_MODEL", raising=False)
    monkeypatch.setenv("AGFLOW_MODEL_PRO", "p")
    monkeypatch.setenv("AGFLOW_MODEL_FLASH", "f")
    monkeypatch.setenv("AGFLOW_MODEL_ULTRA", "u")
    assert model_for_tier("pro") == "p" and model_for_tier("flash") == "f"
    assert model_for_tier("ultra") is None  # not a known tier yet: falls back to AGFLOW_MODEL (unset)
    override(home, {"agent_models": {"add": ["ultra"]}})
    assert model_for_tier("ultra") == "u"  # a new tier needs no code change
    assert model_for_tier("inherit") is None and model_for_tier(None) is None
    monkeypatch.setenv("AGFLOW_MODEL", "default")
    assert model_for_tier("inherit") == "default" and model_for_tier("unheard-of") == "default"
