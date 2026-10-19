"""Share skills and MCP servers with Gemini CLI (GEMINI.md at ~/.gemini is already shared).

Only items agflow synced are updated or removed on later runs (tracked in sync-gemini-cli.json).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agflow import paths
from agflow.fsutil import read_json, write_json
from agflow.inventory import Inventory
from agflow.models import Kind


class SyncError(RuntimeError):
    """The sync target is not in the shape agflow expects. Nothing has been written."""


@dataclass
class SyncReport:
    actions: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # context from the probe (for example Gemini CLI's version)


def _load_json_object(path: Path, what: str) -> dict[str, Any]:
    """The JSON object in ``path`` ({} when absent or empty); anything else is a SyncError that says how to fix it."""
    if path.exists() and not path.is_file():
        raise SyncError(f"{what} {path} is not a file; expected a JSON file. Nothing was written.")
    try:
        data = read_json(path, {})
    except json.JSONDecodeError as exc:
        raise SyncError(f"{what} {path} is not valid JSON ({exc}). Fix it or move it aside and run the sync again. "
                        "Nothing was written.") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise SyncError(f"{what} {path} cannot be read ({exc}). Nothing was written.") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise SyncError(f"{what} {path} must contain a JSON object, found {type(data).__name__}. "
                        "Nothing was written.")
    return data


def _gemini_cli_version() -> str | None:
    """One line of `gemini --version`, only when Gemini CLI is on PATH; never a reason to fail."""
    exe = shutil.which("gemini")
    if exe is None:
        return None
    try:
        done = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=3, check=False,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"found {exe}, but `gemini --version` did not answer ({type(exc).__name__})"
    line = (done.stdout or done.stderr or "").strip().splitlines()[:1]
    return f"Gemini CLI {line[0]} ({exe})" if line else f"found {exe}, but `gemini --version` printed nothing"


def probe_gemini_cli(settings_file: Path, skills_dest: Path, track_file: Path | None = None) -> list[str]:
    """Check Gemini CLI's side of the sync before anything is written, and return notes for the user.

    agflow writes into files that another product owns and whose format it does not control, so it first checks that
    they are what it expects: the settings must be a JSON object whose ``mcpServers`` (when present) is an object of
    objects, the skills destination must be a directory (or not exist yet) and agflow's own record of what it synced
    must be intact. Otherwise a SyncError says which file is wrong and what was expected, and nothing is touched.
    """
    settings = _load_json_object(settings_file, "Gemini CLI's settings file")
    if "mcpServers" in settings:
        servers = settings["mcpServers"]
        if not isinstance(servers, dict):
            raise SyncError(f"`mcpServers` in {settings_file} must be an object mapping server names to their "
                            f"settings, found {type(servers).__name__}. Fix it and run the sync again. "
                            "Nothing was written.")
        bad = sorted(str(name) for name, cfg in servers.items() if not isinstance(cfg, dict))
        if bad:
            raise SyncError(f"`mcpServers` in {settings_file} must map each server to an object; these do not: "
                            f"{', '.join(bad)}. Fix them and run the sync again. Nothing was written.")
    if skills_dest.exists() and not skills_dest.is_dir():
        raise SyncError(f"{skills_dest} exists but is not a directory; Gemini CLI keeps one folder per skill there. "
                        "Move it aside and run the sync again. Nothing was written.")
    if track_file is not None:
        record = _load_json_object(track_file, "agflow's record of what it synced")
        wrong = [k for k in ("skills", "mcp") if k in record and not isinstance(record[k], list)]
        if wrong:
            raise SyncError(f"agflow's record {track_file} must hold lists under {', '.join(wrong)}. Delete it to "
                            "treat everything as unmanaged and run the sync again. Nothing was written.")
    version = _gemini_cli_version()
    return [version] if version else []


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
    report.notes = probe_gemini_cli(settings_file, skills_dest, track_file)  # before any write, dry run included
    record = read_json(track_file, {}) or {}
    tracked = {"skills": list(record.get("skills") or []), "mcp": list(record.get("mcp") or [])}

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
