"""Share skills and MCP servers with Gemini CLI (GEMINI.md at ~/.gemini is already shared).

Only items agflow synced are updated or removed on later runs (tracked in sync-gemini-cli.json).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agflow import paths
from agflow.fsutil import read_json, write_json
from agflow.inventory import Inventory
from agflow.models import Kind


@dataclass
class SyncReport:
    actions: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def to_gemini_cli_server(cfg: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in cfg.items() if k in ("command", "args", "env", "cwd", "timeout", "headers")}
    if cfg.get("serverUrl"):
        out["httpUrl"] = cfg["serverUrl"]
    elif cfg.get("url"):
        out["url"] = cfg["url"]
    return out


def sync_gemini_cli(workspace: Path | None, *, project: bool = False, dry_run: bool = False) -> SyncReport:
    """Copy enabled Antigravity skills + MCP servers into Gemini CLI's user (or project) config."""
    report = SyncReport()
    target_ws = workspace if project else None
    skills_dest = paths.gemini_cli_skills_dir(target_ws)
    settings_file = paths.gemini_cli_settings(target_ws)
    track_file = (paths.workspace_state_dir(workspace) if project and workspace else paths.global_state_dir()) \
        / "sync-gemini-cli.json"
    tracked = read_json(track_file, {"skills": [], "mcp": []}) or {"skills": [], "mcp": []}

    inv = Inventory.discover(workspace if project else None, include_legacy=False)
    skills = {}
    for item in inv.of(Kind.SKILL):
        skills.setdefault(item.name, item)  # precedence order: first wins
    for name, item in sorted(skills.items()):
        dest = skills_dest / name
        if dest.exists() and name not in tracked["skills"]:
            report.skipped.append(f"skill {name}: {dest} exists and is not managed by agflow")
            continue
        report.actions.append(f"skill    {item.path.parent} -> {dest}")
        if not dry_run:
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(item.path.parent, dest, ignore=shutil.ignore_patterns("__pycache__"))
    stale = [n for n in tracked["skills"] if n not in skills]
    for name in stale:
        report.actions.append(f"remove   {skills_dest / name} (no longer present)")
        if not dry_run and (skills_dest / name).exists():
            shutil.rmtree(skills_dest / name)

    settings = read_json(settings_file, {}) or {}
    servers = settings.setdefault("mcpServers", {})
    synced_mcp = []
    for item in inv.of(Kind.MCP):
        if item.name in synced_mcp:
            continue
        if item.name in servers and item.name not in tracked["mcp"]:
            report.skipped.append(f"mcp {item.name}: already configured in {settings_file}")
            continue
        servers[item.name] = to_gemini_cli_server(item.meta.get("config", {}))
        synced_mcp.append(item.name)
        report.actions.append(f"mcp      {item.name} -> {settings_file}")
    for name in tracked["mcp"]:
        if name not in synced_mcp and name in servers:
            del servers[name]
            report.actions.append(f"remove   mcp {name} from {settings_file}")
    if not dry_run:
        write_json(settings_file, settings)
        write_json(track_file, {"skills": sorted(skills), "mcp": synced_mcp})
    return report
