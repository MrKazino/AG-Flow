"""Enable/disable skills, agents and rules by parking them outside Antigravity's discovery paths.

Antigravity has no per-item "disabled" flag for skills/agents/rules, so a disabled item is moved to
``<state_dir>/disabled/<kind>/...`` and recorded in ``disabled/index.json``; enabling moves it back.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from agflow import parking, paths
from agflow.fsutil import read_json, write_json
from agflow.inventory import Inventory
from agflow.models import TOGGLEABLE, Item, Kind

log = logging.getLogger("agflow.toggles")


class ToggleError(RuntimeError):
    pass


def _state_dir_for(item: Item, workspace: Path | None) -> Path:
    if workspace is not None:
        try:
            item.path.resolve().relative_to(workspace.resolve())
            return paths.workspace_state_dir(workspace)
        except ValueError:
            pass
    return paths.global_state_dir()


def _movable(item: Item) -> Path:
    """The filesystem entry that represents the item (skill folder, agent file/folder, rule file)."""
    if item.kind == Kind.SKILL or (item.kind == Kind.AGENT and item.path.name == "agent.md"):
        return item.path.parent
    return item.path


def _index(state_dir: Path) -> tuple[Path, list[dict[str, Any]]]:
    f = state_dir / "disabled" / "index.json"
    return f, list(read_json(f, []) or [])


def disable(inv: Inventory, kind: Kind, name: str) -> Path:
    if kind not in TOGGLEABLE:
        raise ToggleError(f"{kind.value} items can't be toggled this way")
    item = inv.find(kind, name, enabled=True)
    if item is None:
        raise ToggleError(f"no enabled {kind.value} named '{name}'")
    if item.scope == "legacy":
        raise ToggleError("legacy items can't be toggled; run `agflow migrate` first")
    state_dir = _state_dir_for(item, inv.workspace)
    src = _movable(item)
    parked = state_dir / "disabled" / f"{kind.value}s" / item.scope.replace(":", "_") / src.name
    if parked.exists():
        raise ToggleError(f"{parked} already exists; remove it or enable the parked copy first")
    entry = {"kind": kind.value, "name": name, "scope": item.scope,
             "original_path": str(src), "parked_path": str(parked)}
    f, index = _index(state_dir)
    # Journal first, then move: a crash between the two leaves an entry whose parked copy does not exist yet while
    # the item is still in place -- harmless, healed by the next toggle. The other order orphaned the parked item
    # (moved, but unknown to the index): invisible to the inventory and impossible to re-enable.
    index = [e for e in index if not _same_item(e, entry)]  # leftovers of an earlier interrupted disable
    index.append(entry)
    write_json(f, index)
    parked.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(src), str(parked))
    except Exception:
        # nothing moved: take the entry back out. (A hard stop skips this and leaves the harmless stale entry.)
        write_json(f, [e for e in index if e is not entry])
        raise
    # The marker travels with the item. It is written after the move: a crash in between leaves a parked item that the
    # index still knows (harmless), never a marker on something that is still in place.
    try:
        parking.write_marker(parked, entry)
    except OSError as exc:
        log.warning("%s '%s' is disabled but its marker could not be written (%s); the index still records it",
                    kind.value, name, exc)
    return parked


def _same_item(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return (a.get("kind"), a.get("name"), a.get("original_path")) == (b["kind"], b["name"], b["original_path"])


def enable(inv: Inventory, kind: Kind, name: str) -> Path:
    for state_dir in _candidate_state_dirs(inv.workspace):
        f, index = _index(state_dir)
        for entry in index:
            if entry["kind"] == kind.value and entry["name"] == name:
                original, parked = Path(entry["original_path"]), Path(entry["parked_path"])
                if original.exists() and not parked.exists():
                    # the entry of a disable that was interrupted before it moved anything, or of an item that was
                    # put back by hand: it is in place, so it only needs its marker and its entry removed
                    parking.remove_marker(original)
                    index.remove(entry)
                    write_json(f, index)
                    return original
                if original.exists():
                    raise ToggleError(f"{original} exists again; refusing to overwrite it")
                if not parked.exists():
                    raise ToggleError(f"parked copy {parked} is missing")
                parking.remove_marker(parked)  # first, so that a marker only ever exists on a parked (or leaked) item
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(parked), str(original))
                index.remove(entry)
                write_json(f, index)
                return original
    return _enable_by_marker(inv, kind, name)


def _enable_by_marker(inv: Inventory, kind: Kind, name: str) -> Path:
    """Enable an item the index does not list: one that carries its marker in a scanned place (it came back by hand)
    or in the parking area (its entry was lost)."""
    item = inv.find(kind, name, enabled=False)
    if item is None:
        raise ToggleError(f"no disabled {kind.value} named '{name}'")
    if "leaked_marker" in item.meta:
        movable = _movable(item)  # the item sits in a scanned folder: its SKILL.md / agent file / rule file
        parking.remove_marker(movable)  # already where Antigravity looks; only the claim that it is disabled goes
        return movable
    if item.meta.get("recovered"):
        parked = item.path  # a parked item's path is the parked folder or file itself
        origin = item.meta.get("original_path")
        if not origin:
            raise ToggleError(f"{parked} is parked but its marker does not say where it came from; move it back "
                              "by hand and delete the .agflow-disabled marker")
        original = Path(origin)
        if original.exists():
            raise ToggleError(f"{original} exists again; refusing to overwrite it")
        parking.remove_marker(parked)
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(parked), str(original))
        return original
    raise ToggleError(f"no disabled {kind.value} named '{name}'")


def _candidate_state_dirs(workspace: Path | None) -> list[Path]:
    dirs = [paths.workspace_state_dir(workspace)] if workspace is not None else []
    return [*dirs, paths.global_state_dir()]


def apply_profile(workspace: Path, selection: dict[str, list[str]]) -> list[str]:
    """Make workspace-scope skills/agents (incl. workspace plugins) match a profile selection."""
    actions: list[str] = []
    inv = Inventory.discover(workspace, include_global=False, include_legacy=False)
    root = workspace.resolve()

    def in_workspace(item: Item) -> bool:
        # never touch global plugins/skills: a workspace profile must not change other workspaces
        path = Path(item.meta.get("original_path") or item.path).resolve()
        return path == root or root in path.parents

    for kind, key in ((Kind.SKILL, "skills"), (Kind.AGENT, "agents")):
        wanted = set(selection.get(key, []))
        for item in inv.of(kind, enabled=None):
            if not in_workspace(item):
                continue
            if item.enabled and item.name not in wanted:
                disable(inv, kind, item.name)
                actions.append(f"disable {kind.value} {item.name}")
            elif not item.enabled and item.name in wanted:
                enable(inv, kind, item.name)
                actions.append(f"enable  {kind.value} {item.name}")
    if selection.get("mcp"):
        from agflow import mcp_registry

        mcp_file = paths.workspace_layout(workspace).mcp_file
        assert mcp_file is not None
        try:
            for name, cfg in mcp_registry.list_servers(mcp_file).items():
                want = name in selection["mcp"] or name == "agflow"
                if bool(cfg.get("disabled")) == want:
                    mcp_registry.set_enabled(mcp_file, name, want)
                    actions.append(f"{'enable ' if want else 'disable'} mcp {name}")
        except mcp_registry.McpError as exc:  # callers (CLI, dashboard) already handle ToggleError
            raise ToggleError(str(exc)) from exc
    return actions
