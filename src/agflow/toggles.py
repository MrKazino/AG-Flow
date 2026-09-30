"""Enable/disable skills, agents and rules by parking them outside Antigravity's discovery paths.

Antigravity has no per-item "disabled" flag for skills/agents/rules, so a disabled item is moved to
``<state_dir>/disabled/<kind>/...`` and recorded in ``disabled/index.json``; enabling moves it back.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from agflow import paths
from agflow.fsutil import read_json, write_json
from agflow.inventory import Inventory
from agflow.models import TOGGLEABLE, Item, Kind


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
    parked.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(parked))
    f, index = _index(state_dir)
    index.append({"kind": kind.value, "name": name, "scope": item.scope,
                  "original_path": str(src), "parked_path": str(parked)})
    write_json(f, index)
    return parked


def enable(inv: Inventory, kind: Kind, name: str) -> Path:
    for state_dir in _candidate_state_dirs(inv.workspace):
        f, index = _index(state_dir)
        for entry in index:
            if entry["kind"] == kind.value and entry["name"] == name:
                original, parked = Path(entry["original_path"]), Path(entry["parked_path"])
                if original.exists():
                    raise ToggleError(f"{original} exists again; refusing to overwrite it")
                if not parked.exists():
                    raise ToggleError(f"parked copy {parked} is missing")
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(parked), str(original))
                index.remove(entry)
                write_json(f, index)
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
        for name, cfg in mcp_registry.list_servers(mcp_file).items():
            want = name in selection["mcp"] or name == "agflow"
            if bool(cfg.get("disabled")) == want:
                mcp_registry.set_enabled(mcp_file, name, want)
                actions.append(f"{'enable ' if want else 'disable'} mcp {name}")
    return actions
