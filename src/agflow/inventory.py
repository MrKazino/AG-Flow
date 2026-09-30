"""Discover every Antigravity component across global, workspace, plugin and legacy scopes."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agflow import frontmatter, paths
from agflow.fsutil import read_json
from agflow.models import Item, Kind
from agflow.paths import Layout, Scope


def _meta_of(path: Path) -> tuple[dict[str, Any], str, str | None]:
    """Return (frontmatter, body, parse error)."""
    try:
        meta, body = frontmatter.parse(path.read_text(encoding="utf-8"))
        return meta, body, None
    except (frontmatter.FrontmatterError, UnicodeDecodeError, OSError) as exc:
        return {}, "", str(exc)


def iter_skills(skills_dir: Path | None) -> Iterable[Path]:
    """Yield SKILL.md files: <skills_dir>/<name>/SKILL.md."""
    if not skills_dir or not skills_dir.is_dir():
        return
    for d in sorted(skills_dir.iterdir()):
        if d.is_dir() and (d / "SKILL.md").is_file():
            yield d / "SKILL.md"


def iter_agents(agents_dir: Path | None) -> Iterable[Path]:
    """Yield agent files: <agents_dir>/<name>.md or <agents_dir>/<name>/agent.md."""
    if not agents_dir or not agents_dir.is_dir():
        return
    for p in sorted(agents_dir.iterdir()):
        if p.is_file() and p.suffix == ".md":
            yield p
        elif p.is_dir() and (p / "agent.md").is_file():
            yield p / "agent.md"


def agent_name_from_path(path: Path) -> str:
    return path.parent.name if path.name == "agent.md" else path.stem


def iter_markdown(directory: Path | None) -> Iterable[Path]:
    if not directory or not directory.is_dir():
        return
    yield from sorted(p for p in directory.iterdir() if p.is_file() and p.suffix == ".md")


def iter_graphs(directory: Path | None) -> Iterable[Path]:
    if not directory or not directory.is_dir():
        return
    yield from sorted(p for p in directory.iterdir() if p.is_file() and p.suffix in (".yaml", ".yml"))


@dataclass
class Inventory:
    workspace: Path | None
    items: list[Item] = field(default_factory=list)
    layouts: list[Layout] = field(default_factory=list)
    sources: list[tuple[Path, str]] = field(default_factory=list)  # (layout root, scope label)
    load_errors: list[tuple[Path, str]] = field(default_factory=list)

    # -- queries -------------------------------------------------------------------------
    def of(self, kind: Kind, *, enabled: bool | None = True) -> list[Item]:
        return [i for i in self.items if i.kind == kind and (enabled is None or i.enabled == enabled)]

    def names(self, kind: Kind) -> set[str]:
        return {i.name for i in self.of(kind)}

    def find(self, kind: Kind, name: str, *, enabled: bool | None = True) -> Item | None:
        """First match wins; scopes are ordered by precedence (workspace > plugins > global)."""
        for item in self.of(kind, enabled=enabled):
            if item.name == name:
                return item
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace": str(self.workspace) if self.workspace else None,
            "items": [i.to_dict() for i in self.items],
            "layouts": [{"scope": lay.scope.value, "label": lay.label, "root": str(lay.root)} for lay in self.layouts],
        }

    # -- discovery -----------------------------------------------------------------------
    @classmethod
    def discover(
        cls,
        workspace: Path | None = None,
        *,
        include_global: bool = True,
        include_plugins: bool = True,
        include_legacy: bool = True,
        extra_layouts: Iterable[Layout] = (),
    ) -> Inventory:
        inv = cls(workspace=workspace)
        layouts: list[tuple[Layout, str]] = []
        for lay in extra_layouts:
            layouts.append((lay, f"plugin:{lay.root.name}" if lay.scope == Scope.PLUGIN else lay.scope.value))
        if workspace is not None:
            layouts.append((paths.workspace_layout(workspace), "workspace"))
        if include_plugins:
            for pdir in paths.installed_plugin_dirs(workspace):
                layouts.append((paths.plugin_layout(pdir), paths.plugin_scope_label(pdir, workspace)))
        if include_global:
            layouts.append((paths.global_layout(), "global"))
        if include_legacy:
            if workspace is not None:
                layouts.append((paths.legacy_workspace_layout(workspace), "legacy"))
            if include_global:
                layouts.append((paths.legacy_global_layout(), "legacy"))

        seen_roots: set[Path] = set()
        for layout, scope_label in layouts:
            key = layout.root.resolve() if layout.root.exists() else layout.root
            if key in seen_roots:
                continue
            seen_roots.add(key)
            inv.layouts.append(layout)
            inv.sources.append((layout.root, scope_label))
            if layout.gemini_md is not None:
                inv.sources.append((layout.gemini_md, scope_label))
            inv._scan_layout(layout, scope_label)
        for state_dir in _state_dirs(workspace, include_global):
            inv._scan_disabled(state_dir)
        return inv

    def _add(self, kind: Kind, name: str, path: Path, scope: str, **kw: Any) -> Item:
        item = Item(kind=kind, name=name, path=path, scope=scope, **kw)
        self.items.append(item)
        return item

    def _scan_layout(self, lay: Layout, scope: str) -> None:
        if lay.scope == Scope.PLUGIN:
            manifest = read_json(lay.root / "plugin.json", {}) or {}
            self._add(
                Kind.PLUGIN,
                manifest.get("name", lay.root.name),
                lay.root / "plugin.json",
                scope,
                description=manifest.get("description", ""),
                meta={"version": manifest.get("version", "")},
            )
        if lay.gemini_md and lay.gemini_md.is_file():
            self._add(Kind.GEMINI_MD, "GEMINI.md", lay.gemini_md, scope,
                      meta={"chars": len(lay.gemini_md.read_text(encoding="utf-8"))})
        for p in iter_markdown(lay.rules_dir):
            meta, _, _ = _meta_of(p)  # parse errors are reported by validate.validate_rule
            self._add(Kind.RULE, p.stem, p, scope, description=str(meta.get("description", "")),
                      meta={"trigger": meta.get("trigger"), "globs": meta.get("globs")})
        for p in iter_skills(lay.skills_dir):
            meta, _, _ = _meta_of(p)
            self._add(Kind.SKILL, str(meta.get("name") or p.parent.name), p, scope,
                      description=str(meta.get("description", "")), meta={"folder": p.parent.name})
        for p in iter_agents(lay.agents_dir):
            meta, _, _ = _meta_of(p)
            self._add(Kind.AGENT, str(meta.get("name") or agent_name_from_path(p)), p, scope,
                      description=str(meta.get("description", "")),
                      meta={k: meta.get(k) for k in ("model", "mainAgent", "subagent", "tools",
                                                      "commandExecutionPolicy")})
        for wdir in lay.workflows_dirs:
            for p in iter_markdown(wdir):
                meta, _, _ = _meta_of(p)
                self._add(Kind.WORKFLOW, p.stem, p, scope, description=str(meta.get("description", "")))
        if lay.mcp_file and lay.mcp_file.is_file():
            try:
                servers = (read_json(lay.mcp_file, {}) or {}).get("mcpServers", {}) or {}
            except (json.JSONDecodeError, AttributeError) as exc:
                self.load_errors.append((lay.mcp_file, f"invalid JSON: {exc}"))
                servers = {}
            for name, cfg in servers.items() if isinstance(servers, dict) else []:
                cfg = cfg if isinstance(cfg, dict) else {}
                self._add(Kind.MCP, name, lay.mcp_file, scope, enabled=not cfg.get("disabled", False),
                          description=_describe_mcp(cfg), meta={"config": cfg})
        if lay.hooks_file and lay.hooks_file.is_file():
            try:
                hooks = (read_json(lay.hooks_file, {}) or {}).get("hooks", {}) or {}
            except (json.JSONDecodeError, AttributeError) as exc:
                self.load_errors.append((lay.hooks_file, f"invalid JSON: {exc}"))
                hooks = {}
            for event, entries in hooks.items() if isinstance(hooks, dict) else []:
                for idx, entry in enumerate(entries if isinstance(entries, list) else []):
                    entry = entry if isinstance(entry, dict) else {}
                    self._add(Kind.HOOK, f"{event}#{idx}", lay.hooks_file, scope,
                              description=str(entry.get("command", "")),
                              meta={"event": event, "matcher": entry.get("matcher")})
        for p in iter_graphs(lay.graphs_dir):
            self._add(Kind.GRAPH, p.stem, p, scope)

    def _scan_disabled(self, state_dir: Path) -> None:
        index = read_json(state_dir / "disabled" / "index.json", []) or []
        for entry in index:
            parked = Path(entry["parked_path"])
            if parked.exists():
                self._add(Kind(entry["kind"]), entry["name"], parked, entry.get("scope", "?"),
                          enabled=False, meta={"original_path": entry["original_path"]})


def _state_dirs(workspace: Path | None, include_global: bool) -> list[Path]:
    dirs = []
    if workspace is not None:
        dirs.append(paths.workspace_state_dir(workspace))
    if include_global:
        dirs.append(paths.global_state_dir())
    return dirs


def _describe_mcp(cfg: dict[str, Any]) -> str:
    if "command" in cfg:
        return " ".join([str(cfg.get("command", ""))] + [str(a) for a in cfg.get("args", []) or []])
    return str(cfg.get("serverUrl") or cfg.get("url") or cfg.get("httpUrl") or "")
