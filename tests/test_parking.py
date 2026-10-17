"""Disabled items carry a marker, so "disabled" does not rest on Antigravity never looking where they were put (F34)."""

from __future__ import annotations

import json
import shutil

import pytest

from agflow import paths, toggles
from agflow.inventory import Inventory
from agflow.models import Kind, Level
from agflow.parking import MARKER, marker_path, parked_items
from agflow.validate import validate_inventory
from tests.conftest import write

SKILL = "---\nname: mine\ndescription: " + "d" * 50 + "\n---\nbody\n"
AGENT = "---\nname: helper\ndescription: A helper agent for the parking tests.\nsubagent: true\n---\nWork.\n"
RULE = "---\ntrigger: always_on\ndescription: a rule\n---\nBe careful.\n"


def skill(ws):
    return write(ws / ".agents" / "skills" / "mine" / "SKILL.md", SKILL)


def agent_file(ws):
    return write(ws / ".agents" / "agents" / "helper.md", AGENT)


def agent_folder(ws):
    return write(ws / ".agents" / "agents" / "helper" / "agent.md", AGENT)


def rule(ws):
    return write(ws / ".agents" / "rules" / "careful.md", RULE)


def index_of(ws):
    f = paths.workspace_state_dir(ws) / "disabled" / "index.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else []


def codes(issues, level=None):
    return {i.code for i in issues if level is None or i.level == level}


# --- the marker is written on disable and removed on enable --------------------------------------------------------


def test_a_parked_skill_folder_carries_a_marker_inside_it(workspace):
    skill(workspace)
    parked = toggles.disable(Inventory.discover(workspace), Kind.SKILL, "mine")
    marker = json.loads((parked / MARKER).read_text(encoding="utf-8"))
    assert (marker["kind"], marker["name"], marker["scope"]) == ("skill", "mine", "workspace")
    assert marker["original_path"] == str(workspace / ".agents" / "skills" / "mine")
    assert marker["time"] > 0


@pytest.mark.parametrize(("make", "kind", "name"), [(agent_file, Kind.AGENT, "helper"), (rule, Kind.RULE, "careful")])
def test_a_parked_file_carries_a_sidecar_marker_beside_it(workspace, make, kind, name):
    make(workspace)
    parked = toggles.disable(Inventory.discover(workspace), kind, name)
    assert parked.is_file() and marker_path(parked) == parked.with_name(parked.name + MARKER)
    assert json.loads(marker_path(parked).read_text(encoding="utf-8"))["name"] == name


def test_a_parked_agent_folder_carries_the_marker_inside_it(workspace):
    agent_folder(workspace)
    parked = toggles.disable(Inventory.discover(workspace), Kind.AGENT, "helper")
    assert parked.is_dir() and (parked / MARKER).is_file()


@pytest.mark.parametrize(("make", "kind", "name"), [(skill, Kind.SKILL, "mine"), (agent_file, Kind.AGENT, "helper"),
                                                    (agent_folder, Kind.AGENT, "helper"), (rule, Kind.RULE, "careful")])
def test_enable_removes_the_marker_wherever_it_was(workspace, make, kind, name):
    original = make(workspace)
    parked = toggles.disable(Inventory.discover(workspace), kind, name)
    marker = marker_path(parked)
    assert marker.exists()
    toggles.enable(Inventory.discover(workspace), kind, name)
    assert original.exists() and not marker.exists() and not (original.parent / MARKER).exists()
    assert not list((paths.workspace_state_dir(workspace) / "disabled").rglob(f"*{MARKER}"))
    assert index_of(workspace) == []
    assert not any("leaked_marker" in i.meta for i in Inventory.discover(workspace).items)


def test_a_marker_that_cannot_be_written_does_not_undo_the_disable(workspace, monkeypatch, caplog):
    skill(workspace)
    monkeypatch.setattr(toggles.parking, "write_marker", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")))
    with caplog.at_level("WARNING", logger="agflow.toggles"):
        parked = toggles.disable(Inventory.discover(workspace), Kind.SKILL, "mine")
    assert parked.is_dir() and len(index_of(workspace)) == 1
    assert "marker could not be written" in caplog.text
    assert Inventory.discover(workspace).find(Kind.SKILL, "mine", enabled=False) is not None


def test_a_failed_move_leaves_no_marker_anywhere(workspace, monkeypatch):
    original = skill(workspace)
    monkeypatch.setattr(toggles.shutil, "move", lambda *a, **k: (_ for _ in ()).throw(OSError("in use")))
    with pytest.raises(OSError, match="in use"):
        toggles.disable(Inventory.discover(workspace), Kind.SKILL, "mine")
    assert original.exists() and not (original.parent / MARKER).exists() and index_of(workspace) == []


# --- a disabled item that is back in a scanned folder is reported ---------------------------------------------------


def _leak(ws, make=skill, kind=Kind.SKILL, name="mine"):
    """Disable an item, then put it back by hand (a backup restore, a careless move, a git checkout)."""
    original = make(ws)
    movable = original.parent if make in (skill, agent_folder) else original
    parked = toggles.disable(Inventory.discover(ws), kind, name)
    shutil.move(str(parked), str(movable))
    if make in (agent_file, rule):  # the sidecar stays in the parking area; a restore would bring it along
        shutil.move(str(marker_path(parked)), str(marker_path(movable))) if marker_path(parked).exists() else None
    return original


@pytest.mark.parametrize(("make", "kind", "name"), [(skill, Kind.SKILL, "mine"), (agent_folder, Kind.AGENT, "helper"),
                                                    (rule, Kind.RULE, "careful")])
def test_a_marked_item_in_a_scanned_folder_is_disabled_for_agflow_and_flagged_by_doctor(workspace, make, kind, name):
    _leak(workspace, make, kind, name)
    inv = Inventory.discover(workspace)
    assert inv.find(kind, name) is None, "agflow's own tooling must not use something it was told is disabled"
    item = inv.find(kind, name, enabled=False)
    assert item is not None and "leaked_marker" in item.meta
    leak = [i for i in validate_inventory(inv) if i.code == "disabled.marker_leak"]
    assert len(leak) == 1 and leak[0].level == Level.WARNING
    assert name in leak[0].message and "Antigravity loads it" in leak[0].message
    assert f"enable {name}" in leak[0].message, "the message names the way out"


def test_enabling_a_leaked_item_removes_the_marker_and_the_stale_entry(workspace):
    original = _leak(workspace)
    toggles.enable(Inventory.discover(workspace), Kind.SKILL, "mine")
    assert original.is_file() and not (original.parent / MARKER).exists() and index_of(workspace) == []
    inv = Inventory.discover(workspace)
    assert inv.find(Kind.SKILL, "mine") is not None
    assert "disabled.marker_leak" not in codes(validate_inventory(inv))


def test_a_leaked_item_is_still_found_when_the_index_entry_is_gone_too(workspace):
    original = _leak(workspace)
    (paths.workspace_state_dir(workspace) / "disabled" / "index.json").unlink()
    inv = Inventory.discover(workspace)
    assert inv.find(Kind.SKILL, "mine", enabled=False) is not None
    toggles.enable(inv, Kind.SKILL, "mine")
    assert original.is_file() and not (original.parent / MARKER).exists()


# --- a parked item whose index entry is lost is recovered from its marker ------------------------------------------


@pytest.mark.parametrize(("make", "kind", "name"), [(skill, Kind.SKILL, "mine"), (agent_file, Kind.AGENT, "helper"),
                                                    (rule, Kind.RULE, "careful")])
def test_a_parked_item_without_an_index_entry_is_listed_and_can_be_enabled(workspace, make, kind, name):
    original = make(workspace)
    toggles.disable(Inventory.discover(workspace), kind, name)
    (paths.workspace_state_dir(workspace) / "disabled" / "index.json").unlink()
    inv = Inventory.discover(workspace)
    item = inv.find(kind, name, enabled=False)
    assert item is not None and item.meta.get("recovered") is True
    toggles.enable(inv, kind, name)
    assert original.exists()
    assert not list((paths.workspace_state_dir(workspace) / "disabled").rglob(f"*{MARKER}"))


def test_a_corrupt_index_no_longer_hides_what_is_parked(workspace):
    skill(workspace)
    toggles.disable(Inventory.discover(workspace), Kind.SKILL, "mine")
    (paths.workspace_state_dir(workspace) / "disabled" / "index.json").write_text("{not json", encoding="utf-8")
    inv = Inventory.discover(workspace)
    assert inv.load_errors, "the corrupt index is still reported"
    assert inv.find(Kind.SKILL, "mine", enabled=False) is not None
    (paths.workspace_state_dir(workspace) / "disabled" / "index.json").write_text('{"not": "a list"}', encoding="utf-8")
    assert Inventory.discover(workspace).find(Kind.SKILL, "mine", enabled=False) is not None


def test_an_item_that_is_listed_in_the_index_is_not_listed_twice(workspace):
    skill(workspace)
    toggles.disable(Inventory.discover(workspace), Kind.SKILL, "mine")
    assert len(Inventory.discover(workspace).of(Kind.SKILL, enabled=False)) == 1


def test_a_recovered_item_is_not_moved_over_an_existing_one(workspace):
    original = skill(workspace)
    toggles.disable(Inventory.discover(workspace), Kind.SKILL, "mine")
    (paths.workspace_state_dir(workspace) / "disabled" / "index.json").unlink()
    write(original, "someone created a new one\n")
    with pytest.raises(toggles.ToggleError, match="exists again"):
        toggles.enable(Inventory.discover(workspace), Kind.SKILL, "mine")
    assert original.read_text(encoding="utf-8") == "someone created a new one\n"


def test_an_unreadable_marker_still_means_disabled_but_cannot_say_where_it_came_from(workspace):
    skill(workspace)
    parked = toggles.disable(Inventory.discover(workspace), Kind.SKILL, "mine")
    (parked / MARKER).write_text("garbage", encoding="utf-8")
    (paths.workspace_state_dir(workspace) / "disabled" / "index.json").unlink()
    inv = Inventory.discover(workspace)
    item = inv.find(Kind.SKILL, "mine", enabled=False)
    assert item is not None, "the folder name still says what it is"
    with pytest.raises(toggles.ToggleError, match="does not say where it came from"):
        toggles.enable(inv, Kind.SKILL, "mine")
    assert parked.is_dir()


def test_parked_items_lists_each_marked_item_once(workspace):
    skill(workspace)
    agent_file(workspace)
    inv = Inventory.discover(workspace)
    toggles.disable(inv, Kind.SKILL, "mine")
    toggles.disable(inv, Kind.AGENT, "helper")
    found = parked_items(paths.workspace_state_dir(workspace) / "disabled")
    assert sorted(m["name"] for _, m in found) == ["helper", "mine"]


# --- the parking area must not sit in a scanned folder -------------------------------------------------------------


def test_doctor_is_quiet_about_the_normal_arrangement(workspace):
    skill(workspace)
    toggles.disable(Inventory.discover(workspace), Kind.SKILL, "mine")
    found = codes(validate_inventory(Inventory.discover(workspace)))
    assert not found & {"disabled.marker_leak", "disabled.in_scan_scope"}


def test_a_parking_area_inside_a_scanned_folder_is_flagged(workspace, monkeypatch):
    monkeypatch.setattr(paths, "workspace_state_dir", lambda ws: ws / ".agents" / "skills" / "agflow-state")
    inv = Inventory.discover(workspace)
    issues = [i for i in validate_inventory(inv) if i.code == "disabled.in_scan_scope"]
    assert len(issues) == 1 and issues[0].level == Level.WARNING
    assert "still loaded" in issues[0].message and str(workspace / ".agents" / "skills") in issues[0].message


# --- what was parked before markers existed keeps working ---------------------------------------------------------


def test_an_item_parked_by_an_older_version_without_a_marker_still_enables(workspace):
    original = skill(workspace)
    state = paths.workspace_state_dir(workspace)
    parked = state / "disabled" / "skills" / "workspace" / "mine"
    parked.parent.mkdir(parents=True)
    shutil.move(str(original.parent), str(parked))
    write(state / "disabled" / "index.json", json.dumps([{
        "kind": "skill", "name": "mine", "scope": "workspace", "original_path": str(original.parent),
        "parked_path": str(parked)}]))
    inv = Inventory.discover(workspace)
    assert inv.find(Kind.SKILL, "mine", enabled=False) is not None
    assert "disabled.marker_leak" not in codes(validate_inventory(inv))
    toggles.enable(inv, Kind.SKILL, "mine")
    assert original.is_file()


def test_profiles_still_toggle_through_the_marker_aware_code(workspace):
    skill(workspace)
    actions = toggles.apply_profile(workspace, {"skills": [], "agents": []})
    assert any("disable skill mine" in a for a in actions)
    actions = toggles.apply_profile(workspace, {"skills": ["mine"], "agents": []})
    assert any("enable  skill mine" in a for a in actions)
    assert (workspace / ".agents" / "skills" / "mine" / "SKILL.md").is_file()
