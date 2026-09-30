"""Workflow profiles: named bundles of skills / agents / MCP servers activated per workspace.

Profiles come from the bundled ``plugin/profiles.yaml`` and may be extended or overridden by
``<ws>/.agents/agflow/profiles.yaml`` (same schema)::

    profiles:
      frontend:
        description: UI work with browser verification
        extends: core
        skills: [ui-build-verify, a11y-audit, visual-regression]
        agents: [ui-verifier]
        mcp: [chrome-devtools]        # optional: workspace MCP servers to keep enabled
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agflow import paths
from agflow.fsutil import read_yaml


class ProfileError(ValueError):
    pass


def load_profiles(source: Path | None = None, workspace: Path | None = None) -> dict[str, dict[str, Any]]:
    try:
        src = source or paths.bundled_plugin_dir()
    except FileNotFoundError:
        src = None
    profiles: dict[str, dict[str, Any]] = {}
    if src is not None:
        profiles.update((read_yaml(src / "profiles.yaml", {}) or {}).get("profiles", {}) or {})
    if workspace is not None:
        extra = (read_yaml(paths.workspace_state_dir(workspace) / "profiles.yaml", {}) or {}).get("profiles", {})
        profiles.update(extra or {})
    return profiles


def _all_names(src: Path, folder: str) -> list[str]:
    base = src / folder
    if not base.is_dir():
        return []
    return sorted(p.stem if p.is_file() else p.name for p in base.iterdir()
                  if (p.is_dir() and (p / "SKILL.md").exists()) or (p.is_file() and p.suffix == ".md")
                  or (p.is_dir() and (p / "agent.md").exists()))


def resolve_profile(name: str, source: Path | None = None, workspace: Path | None = None,
                    _seen: tuple[str, ...] = ()) -> dict[str, list[str]]:
    src = source or paths.bundled_plugin_dir()
    profiles = load_profiles(src, workspace)
    if name not in profiles:
        raise ProfileError(f"unknown profile '{name}'. Available: {', '.join(sorted(profiles)) or 'none'}")
    if name in _seen:
        raise ProfileError(f"profile inheritance cycle: {' -> '.join((*_seen, name))}")
    spec = profiles[name] or {}
    out: dict[str, list[str]] = {"skills": [], "agents": [], "mcp": []}
    if spec.get("extends"):
        parent = resolve_profile(spec["extends"], src, workspace, (*_seen, name))
        for k in out:
            out[k] = list(parent.get(k, []))
    for key, folder in (("skills", "skills"), ("agents", "agents")):
        value = spec.get(key, [])
        names = _all_names(src, folder) if value == "*" else list(value or [])
        out[key] = sorted(set(out[key]) | set(names))
    out["mcp"] = sorted(set(out["mcp"]) | set(spec.get("mcp", []) or []))
    # every orchestrated profile needs the orchestrator
    if out["agents"] and "orchestrator" not in out["agents"] and "orchestrator" in _all_names(src, "agents"):
        out["agents"].append("orchestrator")
    return out
