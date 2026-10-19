"""Discover every Antigravity component across global, workspace, plugin and legacy scopes."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agflow import frontmatter, hookfile, parking, paths
from agflow.fsutil import read_json
from agflow.models import TOGGLEABLE, Item, Kind
from agflow.paths import Layout, Scope


def _meta_of(path: Path) -> tuple[dict[str, Any], str, int, str | None]:
    """Return (frontmatter, body, size in characters, error); the file is read once."""
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        return {}, "", 0, str(exc)
    try:
        meta, body = frontmatter.parse(text)
    except frontmatter.FrontmatterError as exc:
        return {}, "", len(text), str(exc)
    return meta, body, len(text), None


def _leak(movable: Path) -> dict[str, Any]:
    """Item fields for something agflow disabled that is nevertheless in a scanned location (it carries its marker).

    Antigravity does not know the marker and loads such an item; agflow reports it as disabled, and doctor says so
    (``disabled.marker_leak``).
    """
    if not parking.has_marker(movable):
        return {}
    return {"leaked_marker": parking.read_marker(movable)}


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
    # disabled-index entries whose parked copy is gone: (index file, entry, original still in place?)
    stale_disabled: list[tuple[Path, dict[str, Any], bool]] = field(default_factory=list)
    state_dirs: list[Path] = field(default_factory=list)  # where parked items were looked for

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
            inv.state_dirs.append(state_dir)
            inv._scan_disabled(state_dir)
        return inv

    def _add(self, kind: Kind, name: str, path: Path, scope: str, **kw: Any) -> Item:
        item = Item(kind=kind, name=name, path=path, scope=scope, **kw)
        self.items.append(item)
        return item

    def _json(self, path: Path, default: Any) -> Any:
        """Read a JSON file whose content agflow does not control.

        Discovery must survive any single bad file (doctor, the dashboard and the hooks all start from it), so a
        file that cannot be read or parsed is recorded in ``load_errors`` -- where doctor reports it -- and
        ``default`` is returned.
        """
        try:
            return read_json(path, default)
        except json.JSONDecodeError as exc:
            self.load_errors.append((path, f"invalid JSON: {exc}"))
        except UnicodeDecodeError as exc:
            self.load_errors.append((path, f"not valid UTF-8 text: {exc}"))
        except OSError as exc:
            self.load_errors.append((path, f"unreadable: {exc}"))
        return default

    def _json_object(self, path: Path) -> dict[str, Any]:
        data = self._json(path, {}) or {}
        if not isinstance(data, dict):
            self.load_errors.append((path, f"expected a JSON object, got {type(data).__name__}"))
            return {}
        return data

    def _scan_layout(self, lay: Layout, scope: str) -> None:
        if lay.scope == Scope.PLUGIN:
            manifest = self._json_object(lay.root / "plugin.json")
            self._add(
                Kind.PLUGIN,
                manifest.get("name", lay.root.name),
                lay.root / "plugin.json",
                scope,
                description=manifest.get("description", ""),
                meta={"version": manifest.get("version", "")},
            )
        if lay.gemini_md and lay.gemini_md.is_file():
            unreadable = False
            try:
                chars = len(lay.gemini_md.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError) as exc:
                self.load_errors.append((lay.gemini_md, f"unreadable: {exc}"))
                chars, unreadable = 0, True
            self._add(Kind.GEMINI_MD, "GEMINI.md", lay.gemini_md, scope,
                      meta={"chars": chars, **({"unreadable": True} if unreadable else {})})
        for p in iter_markdown(lay.rules_dir):
            meta, _, chars, _ = _meta_of(p)  # parse errors are reported by validate.validate_rule
            leak = _leak(p)
            self._add(Kind.RULE, p.stem, p, scope, description=str(meta.get("description", "")), enabled=not leak,
                      meta={"trigger": meta.get("trigger"), "globs": meta.get("globs"), "chars": chars, **leak})
        for p in iter_skills(lay.skills_dir):
            meta, *_ = _meta_of(p)
            leak = _leak(p.parent)
            self._add(Kind.SKILL, str(meta.get("name") or p.parent.name), p, scope,
                      description=str(meta.get("description", "")), enabled=not leak,
                      meta={"folder": p.parent.name, **leak})
        for p in iter_agents(lay.agents_dir):
            meta, *_ = _meta_of(p)
            leak = _leak(p.parent if p.name == "agent.md" else p)
            self._add(Kind.AGENT, str(meta.get("name") or agent_name_from_path(p)), p, scope,
                      description=str(meta.get("description", "")), enabled=not leak,
                      meta={**{k: meta.get(k) for k in ("model", "mainAgent", "subagent", "tools",
                                                         "commandExecutionPolicy")}, **leak})
        for wdir in lay.workflows_dirs:
            for p in iter_markdown(wdir):
                meta, *_ = _meta_of(p)
                self._add(Kind.WORKFLOW, p.stem, p, scope, description=str(meta.get("description", "")))
        if lay.mcp_file and lay.mcp_file.is_file():
            servers = self._json_object(lay.mcp_file).get("mcpServers", {}) or {}
            for name, cfg in servers.items() if isinstance(servers, dict) else []:
                cfg = cfg if isinstance(cfg, dict) else {}
                self._add(Kind.MCP, name, lay.mcp_file, scope, enabled=not cfg.get("disabled", False),
                          description=_describe_mcp(cfg), meta={"config": cfg})
        if lay.hooks_file and lay.hooks_file.is_file():
            for ref in hookfile.handlers(self._json_object(lay.hooks_file)):
                self._add(Kind.HOOK, f"{ref.hook}:{ref.event}#{ref.number}", lay.hooks_file, scope,
                          description=str(ref.handler.get("command", "")), enabled=ref.enabled,
                          meta={"event": ref.event, "matcher": ref.matcher, "hook": ref.hook, "inert": ref.inert})
        for p in iter_graphs(lay.graphs_dir):
            self._add(Kind.GRAPH, p.stem, p, scope)

    def _scan_disabled(self, state_dir: Path) -> None:
        index_file = state_dir / "disabled" / "index.json"
        index = self._json(index_file, []) or []
        if not isinstance(index, list):
            self.load_errors.append((index_file, f"expected a JSON list of entries, got {type(index).__name__}"))
            index = []  # the markers below can still list what is parked
        for n, entry in enumerate(index):
            try:
                kind, name = Kind(entry["kind"]), entry["name"]
                parked, original = Path(entry["parked_path"]), entry["original_path"]
            except (KeyError, TypeError, ValueError) as exc:
                # one bad entry must not hide the others: report it and keep going
                self.load_errors.append((index_file, f"entry #{n}: {type(exc).__name__}: {exc}"))
                continue
            if parked.exists():
                self._add(kind, name, parked, entry.get("scope", "?"), enabled=False,
                          meta={"original_path": original})
            else:
                self.stale_disabled.append((index_file, entry, Path(str(original)).exists()))
        # A parked item whose index entry was lost (or never written) still carries its marker: list it, so that it is
        # neither invisible nor impossible to enable. The folder it sits in names its kind if the marker cannot.
        indexed = [e["parked_path"] for e in index if isinstance(e, dict) and isinstance(e.get("parked_path"), str)]
        for movable, marker in parking.parked_items(state_dir / "disabled"):
            if any(parking.same_path(movable, p) for p in indexed):
                continue
            kind_dir = movable.parent.parent.name
            try:
                kind = Kind(marker.get("kind") or kind_dir.removesuffix("s"))
            except ValueError:
                continue
            if kind not in TOGGLEABLE:
                continue
            self._add(kind, str(marker.get("name") or movable.stem), movable, str(marker.get("scope") or "?"),
                      enabled=False, meta={"original_path": marker.get("original_path"), "recovered": True})


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
