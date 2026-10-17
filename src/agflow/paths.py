"""Single source of truth for where Antigravity and Gemini look for configuration.

Antigravity 2.x layout (2026):

    ~/.gemini/GEMINI.md                    global rules (12,000-char cap)
    ~/.gemini/config/skills/<name>/        global skills (Agent Skills standard)
    ~/.gemini/config/agents/<name>.md      global custom agents
    ~/.gemini/config/plugins/<name>/       global plugins
    ~/.gemini/config/hooks.json            global hooks
    ~/.gemini/config/mcp_config.json       global MCP servers
    ~/.gemini/config/global_workflows/     global workflows (deprecated)

    <ws>/.agents/{rules,skills,agents,plugins,workflows}/, hooks.json, mcp_config.json

Legacy 1.x locations (``.agent/`` and ``~/.gemini/antigravity/``) are read for
migration only and never written. If Antigravity moves things again, this is the
only file that needs to change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

GEMINI_MD_CHAR_LIMIT = 12_000
PLUGIN_NAME = "agflow"


class Scope(str, Enum):
    GLOBAL = "global"
    WORKSPACE = "workspace"
    PLUGIN = "plugin"
    LEGACY = "legacy"


def home() -> Path:
    return Path(os.path.expanduser("~"))


def gemini_dir() -> Path:
    return home() / ".gemini"


def global_config_dir() -> Path:
    return gemini_dir() / "config"


def global_state_dir() -> Path:
    """agflow's own global state (install manifests, run index, backups)."""
    return global_config_dir() / "agflow"


def backups_dir() -> Path:
    return global_config_dir() / ".agflow-backups"


def install_tmp_dir(state_dir: Path) -> Path:
    """Staging area for an in-progress plugin swap. Under agflow's state dir, so no directory Antigravity scans
    (a half-built or superseded plugin there would be loaded as a second copy)."""
    return state_dir / "install-tmp"


def workspace_root_dir(workspace: Path) -> Path:
    return workspace / ".agents"


def workspace_state_dir(workspace: Path) -> Path:
    return workspace_root_dir(workspace) / "agflow"


def runs_dir(workspace: Path) -> Path:
    return workspace_state_dir(workspace) / "runs"


# The status index of a workspace's runs, ``<runs>/.index.json``. Written by ``graph.store.RunStore``; read, without
# loading the store, by the hook fast path (``hooks._no_active_run``): one definition so the two cannot drift apart.
RUN_INDEX_FILE = ".index.json"
RUN_INDEX_VERSION = 1


@dataclass(frozen=True)
class Layout:
    """Where each component type lives for one scope."""

    scope: Scope
    root: Path
    gemini_md: Path | None = None
    rules_dir: Path | None = None
    skills_dir: Path | None = None
    agents_dir: Path | None = None
    plugins_dir: Path | None = None
    workflows_dirs: tuple[Path, ...] = field(default_factory=tuple)
    hooks_file: Path | None = None
    mcp_file: Path | None = None
    graphs_dir: Path | None = None
    state_dir: Path | None = None
    label: str = ""

    @property
    def disabled_dir(self) -> Path:
        """Parking area for disabled skills/agents/rules (outside Antigravity's discovery)."""
        base = self.state_dir or self.root
        return base / "disabled"


def global_layout() -> Layout:
    cfg = global_config_dir()
    return Layout(
        scope=Scope.GLOBAL,
        root=cfg,
        gemini_md=gemini_dir() / "GEMINI.md",
        skills_dir=cfg / "skills",
        agents_dir=cfg / "agents",
        plugins_dir=cfg / "plugins",
        workflows_dirs=(cfg / "global_workflows", cfg / "workflows"),
        hooks_file=cfg / "hooks.json",
        mcp_file=cfg / "mcp_config.json",
        graphs_dir=global_state_dir() / "graphs",
        state_dir=global_state_dir(),
        label="global (~/.gemini/config)",
    )


def workspace_layout(workspace: Path) -> Layout:
    root = workspace_root_dir(workspace)
    return Layout(
        scope=Scope.WORKSPACE,
        root=root,
        rules_dir=root / "rules",
        skills_dir=root / "skills",
        agents_dir=root / "agents",
        plugins_dir=root / "plugins",
        workflows_dirs=(root / "workflows",),
        hooks_file=root / "hooks.json",
        mcp_file=root / "mcp_config.json",
        graphs_dir=workspace_state_dir(workspace) / "graphs",
        state_dir=workspace_state_dir(workspace),
        label=f"workspace ({workspace})",
    )


def plugin_layout(plugin_dir: Path) -> Layout:
    """Layout of a plugin bundle (plugin.json + skills/ agents/ rules/ hooks.json mcp_config.json)."""
    return Layout(
        scope=Scope.PLUGIN,
        root=plugin_dir,
        gemini_md=plugin_dir / "GEMINI.md",
        rules_dir=plugin_dir / "rules",
        skills_dir=plugin_dir / "skills",
        agents_dir=plugin_dir / "agents",
        hooks_file=plugin_dir / "hooks.json",
        mcp_file=plugin_dir / "mcp_config.json",
        graphs_dir=plugin_dir / "graphs",
        label=f"plugin ({plugin_dir.name})",
    )


def legacy_global_layout() -> Layout:
    """Antigravity 1.x global layout (read-only, for migration)."""
    root = gemini_dir() / "antigravity"
    return Layout(
        scope=Scope.LEGACY,
        root=root,
        skills_dir=root / "skills",
        workflows_dirs=(root / "global_workflows",),
        mcp_file=root / "mcp_config.json",
        label="legacy global (~/.gemini/antigravity)",
    )


def legacy_workspace_layout(workspace: Path) -> Layout:
    """Antigravity 1.x workspace layout (``.agent/``, singular)."""
    root = workspace / ".agent"
    return Layout(
        scope=Scope.LEGACY,
        root=root,
        rules_dir=root / "rules",
        skills_dir=root / "skills",
        workflows_dirs=(root / "workflows",),
        label=f"legacy workspace ({root})",
    )


def agy_cli_dir() -> Path:
    """Antigravity CLI (agy) private config dir."""
    return gemini_dir() / "antigravity-cli"


def agy_cli_mcp_file() -> Path:
    return agy_cli_dir() / "mcp_config.json"


# --- Gemini CLI (sync target) ---------------------------------------------------------------


def gemini_cli_settings(workspace: Path | None = None) -> Path:
    if workspace is not None:
        return workspace / ".gemini" / "settings.json"
    return gemini_dir() / "settings.json"


def gemini_cli_skills_dir(workspace: Path | None = None) -> Path:
    if workspace is not None:
        return workspace / ".gemini" / "skills"
    return gemini_dir() / "skills"


# --- Bundled plugin -----------------------------------------------------------------------


def bundled_plugin_dir() -> Path:
    """The plugin shipped with agflow (wheel: agflow/_plugin, source checkout: <repo>/plugin)."""
    here = Path(__file__).resolve().parent
    packaged = here / "_plugin"
    if (packaged / "plugin.json").exists():
        return packaged
    source = here.parents[1] / "plugin"
    if (source / "plugin.json").exists():
        return source
    raise FileNotFoundError("agflow plugin bundle not found (expected agflow/_plugin or <repo>/plugin)")


def installed_plugin_dirs(workspace: Path | None = None) -> list[Path]:
    """All plugin bundles Antigravity would load: workspace first, then global."""
    dirs: list[Path] = []
    candidates: list[Path] = []
    if workspace is not None:
        candidates.append(workspace_layout(workspace).plugins_dir)  # type: ignore[arg-type]
    candidates.append(global_layout().plugins_dir)  # type: ignore[arg-type]
    candidates.append(agy_cli_dir() / "plugins")
    for base in candidates:
        if base.is_dir():
            dirs.extend(sorted(p for p in base.iterdir() if (p / "plugin.json").exists()))
    return dirs


def plugin_location(pdir: Path, workspace: Path | None = None) -> str:
    """'workspace', 'agy-cli' or 'global' — where an installed plugin bundle lives."""
    parent = pdir.parent.resolve() if pdir.parent.exists() else pdir.parent
    if workspace is not None:
        wdir = workspace_layout(workspace).plugins_dir
        if wdir is not None and wdir.exists() and parent == wdir.resolve():
            return "workspace"
    agy = agy_cli_dir() / "plugins"
    if agy.exists() and parent == agy.resolve():
        return "agy-cli"
    return "global"


def plugin_scope_label(pdir: Path, workspace: Path | None = None) -> str:
    """Scope label of an installed plugin: `plugin:<name>` (global) or `plugin:<name>@workspace|agy-cli`."""
    where = plugin_location(pdir, workspace)
    return f"plugin:{pdir.name}" if where == "global" else f"plugin:{pdir.name}@{where}"


def plugin_base_name(scope: str) -> str | None:
    """`plugin:agflow@workspace` -> `agflow`; None for non-plugin scopes."""
    if not scope.startswith("plugin:"):
        return None
    return scope[len("plugin:"):].split("@", 1)[0]


def find_workspace(start: Path | None = None) -> Path:
    """Walk up from ``start`` to the nearest directory that looks like a workspace root."""
    start = (start or Path.cwd()).resolve()
    user_home = home().resolve()
    for candidate in (start, *start.parents):
        if candidate == user_home:
            break  # ~/.agents belongs to other tools, never treat $HOME as a workspace
        if (candidate / ".agents").is_dir() or (candidate / ".git").exists():
            return candidate
    return start
