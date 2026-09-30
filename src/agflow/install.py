"""Install / uninstall the agflow plugin globally or into a workspace, with backups and rollback.

Global install::

    ~/.gemini/config/plugins/agflow/      plugin bundle (skills, agents, rules, hooks, MCP, graphs)
    ~/.gemini/GEMINI.md                   master prompt merged as a managed block

Workspace install::

    <ws>/.agents/plugins/agflow/          plugin bundle
    <ws>/.agents/rules/agflow-master.md   master prompt as an always_on rule

Every file we overwrite is backed up under ~/.gemini/config/.agflow-backups/<timestamp>/ and
recorded in an install manifest so `agflow uninstall` restores the previous state exactly.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from agflow import frontmatter, paths
from agflow.fsutil import atomic_write_text, backup, copy_tree, read_json, remove, timestamp, write_json
from agflow.profiles import resolve_profile

Scope = Literal["global", "workspace"]
BEGIN = "<!-- agflow:begin (managed by `agflow install`; edit plugin/GEMINI.md instead) -->"
END = "<!-- agflow:end -->"
_BLOCK_RE = re.compile(re.escape("<!-- agflow:begin") + r".*?" + re.escape(END) + r"\n?", re.DOTALL)
MASTER_RULE = "agflow-master.md"


class InstallError(RuntimeError):
    pass


@dataclass
class Plan:
    scope: Scope
    target: Path
    actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    manifest: Path | None = None


def agflow_command() -> list[str]:
    """How Antigravity should launch agflow: the executable running right now, as an absolute path.

    GUI apps often lack the shell PATH, and another (older) `agflow` may come first on PATH, so the
    running program is preferred over a PATH lookup.
    """
    exe_name = "agflow.exe" if os.name == "nt" else "agflow"
    candidates: list[Path] = []
    argv0 = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    if argv0 is not None and argv0.stem.lower() == "agflow":
        candidates.append(argv0)
        if os.name == "nt" and not argv0.suffix:
            candidates.append(argv0.with_suffix(".exe"))
    candidates.append(Path(sys.executable).parent / exe_name)  # the console script next to this interpreter
    for candidate in candidates:
        if candidate.is_file():
            return [str(candidate.absolute())]
    found = shutil.which("agflow")
    if found:
        return [str(Path(found).absolute())]
    return [sys.executable, "-m", "agflow"]


def format_command(parts: list[str], *, windows: bool | None = None) -> str:
    """A command line the host OS shell can run (hooks.json, printed hints).

    POSIX: shlex quoting. Windows: `shlex` single quotes are invalid there, so an exe path without
    spaces is used as-is; a path with spaces becomes bare `agflow` when that name resolves to the same
    executable on PATH (quote-free, so it works in cmd.exe and PowerShell alike); otherwise the
    Windows (double-quote) convention is used.
    """
    windows = os.name == "nt" if windows is None else windows
    if not windows:
        return shlex.join(parts)
    exe, *rest = parts
    if " " in exe:
        on_path = shutil.which("agflow")
        if on_path and _same_file(on_path, exe):
            return subprocess.list2cmdline(["agflow", *rest])
    return subprocess.list2cmdline(parts)


def _same_file(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _manifest_path(scope: Scope, workspace: Path | None) -> Path:
    if scope == "global":
        return paths.global_state_dir() / "install.json"
    assert workspace is not None
    return paths.workspace_state_dir(workspace) / "install.json"


def _plugin_target(scope: Scope, workspace: Path | None) -> Path:
    if scope == "global":
        return paths.global_layout().plugins_dir / paths.PLUGIN_NAME  # type: ignore[operator]
    assert workspace is not None
    return paths.workspace_layout(workspace).plugins_dir / paths.PLUGIN_NAME  # type: ignore[operator]


def installed_copies(workspace: Path | None) -> dict[str, Path]:
    """Where agflow is installed right now: {"global": dir, "workspace": dir}."""
    out = {}
    for scope in ("global", "workspace"):
        if scope == "workspace" and workspace is None:
            continue
        target = _plugin_target(scope, workspace)  # type: ignore[arg-type]
        if (target / "plugin.json").is_file():
            out[scope] = target
    return out


def install(scope: Scope, workspace: Path | None = None, *, source: Path | None = None,
            profile: str | None = None, with_hooks: bool = True, with_gemini_md: bool = True,
            link: bool = False, dry_run: bool = False, allow_duplicate: bool = False) -> Plan:
    if scope == "workspace" and workspace is None:
        raise InstallError("workspace install needs a workspace path")
    other = "workspace" if scope == "global" else "global"
    existing = installed_copies(workspace).get(other)
    if existing is not None and not allow_duplicate:
        raise InstallError(
            f"agflow is already installed {other}ly ({existing}). A second copy would make every agflow skill and "
            "agent exist twice. To limit agflow in one workspace use `agflow profile apply <name>`; to move it, "
            f"run `agflow uninstall --{other}` first; or pass --allow-duplicate if you really want both.")
    src = (source or paths.bundled_plugin_dir()).resolve()
    if not (src / "plugin.json").is_file():
        raise InstallError(f"{src} is not a plugin (missing plugin.json)")
    target = _plugin_target(scope, workspace)
    manifest_file = _manifest_path(scope, workspace)
    plan = Plan(scope=scope, target=target, manifest=manifest_file)
    manifest: dict[str, Any] = read_json(manifest_file, None) or {
        "scope": scope, "created": [], "modified": {}, "managed_blocks": []}
    backup_root = paths.backups_dir() / timestamp()
    anchor = paths.home()

    def record_write(path: Path) -> None:
        """Back up ``path`` the first time we touch it; remember whether we created it."""
        key = str(path)
        if key in manifest["created"] or key in manifest["modified"]:
            return
        if path.exists() or path.is_symlink():
            if not dry_run:
                saved = backup(path, backup_root, anchor)
                manifest["modified"][key] = str(saved)
            plan.actions.append(f"backup   {path}")
        else:
            manifest["created"].append(key)

    selection = resolve_profile(profile, src, workspace) if profile else None

    # 1. plugin bundle
    record_write(target)
    plan.actions.append(f"{'link' if link else 'install'}  {src} -> {target}")
    if not dry_run:
        if target.exists() or target.is_symlink():
            remove(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        if link:
            if selection:
                raise InstallError("--link cannot be combined with --profile (a link installs everything)")
            target.symlink_to(src, target_is_directory=True)
        else:
            copy_tree(src, target)
            (target / "GEMINI.md").unlink(missing_ok=True)  # installed separately below
            _filter_to_profile(target, selection, plan)
            _wire_commands(target, with_hooks, plan)
    elif selection:
        plan.actions.append(f"profile  {profile}: {len(selection['skills'])} skills, "
                            f"{len(selection['agents'])} agents")

    # 2. master prompt
    gemini_src = src / "GEMINI.md"
    if with_gemini_md and gemini_src.is_file():
        master = gemini_src.read_text(encoding="utf-8").strip() + "\n"
        if scope == "global":
            _install_global_gemini_md(master, manifest, plan, record_write, dry_run)
        else:
            rule = paths.workspace_layout(workspace).rules_dir / MASTER_RULE  # type: ignore[operator, arg-type]
            record_write(rule)
            plan.actions.append(f"write    {rule} (always_on)")
            if not dry_run:
                atomic_write_text(rule, _master_rule(master))

    if not dry_run:
        manifest["source"] = str(src)
        manifest["profile"] = profile
        manifest["command"] = agflow_command()
        write_json(manifest_file, manifest)
    plan.actions.append(f"manifest {manifest_file}")
    return plan


def _master_rule(master: str) -> str:
    return frontmatter.dump(
        {"trigger": "always_on",
         "description": "agflow master operating prompt (Expert Senior Workflows Manager)"}, master)


def _install_global_gemini_md(master: str, manifest: dict[str, Any], plan: Plan, record_write: Any,
                              dry_run: bool) -> None:
    gpath = paths.global_layout().gemini_md
    assert gpath is not None
    existing = gpath.read_text(encoding="utf-8") if gpath.exists() else ""
    user_part = _BLOCK_RE.sub("", existing).rstrip()
    block = f"{BEGIN}\n{master}{END}\n"
    combined = (user_part + "\n\n" + block) if user_part else block
    if len(combined) > paths.GEMINI_MD_CHAR_LIMIT:
        # Too big to share GEMINI.md with the user's rules: ship it as an always_on plugin rule instead.
        rule = _plugin_target("global", None) / "rules" / MASTER_RULE
        plan.warnings.append(
            f"~/.gemini/GEMINI.md would be {len(combined)} chars (> {paths.GEMINI_MD_CHAR_LIMIT}); installed the "
            f"master prompt as plugin rule {rule} instead. Trim your GEMINI.md and re-run to merge it.")
        plan.actions.append(f"write    {rule} (always_on fallback)")
        if not dry_run:
            atomic_write_text(rule, _master_rule(master))
        if existing != _BLOCK_RE.sub("", existing) and not dry_run:
            atomic_write_text(gpath, user_part + "\n" if user_part else "")
        return
    record_write(gpath)
    if str(gpath) not in manifest["managed_blocks"]:
        manifest["managed_blocks"].append(str(gpath))
    plan.actions.append(f"merge    {gpath} (managed block, {len(combined)} chars)")
    if not dry_run:
        atomic_write_text(gpath, combined)


def _filter_to_profile(target: Path, selection: dict[str, list[str]] | None, plan: Plan) -> None:
    if not selection:
        return
    for kind, folder in (("skills", "skills"), ("agents", "agents")):
        base = target / folder
        if not base.is_dir():
            continue
        keep = set(selection[kind])
        for entry in list(base.iterdir()):
            name = entry.stem if entry.is_file() else entry.name
            if name not in keep:
                remove(entry)
                plan.actions.append(f"skip     {kind[:-1]} {name} (not in profile)")
    _prune_agent_rosters(target / "agents")
    _drop_unrunnable_graphs(target, selection, plan)


def _drop_unrunnable_graphs(target: Path, selection: dict[str, list[str]], plan: Plan) -> None:
    """A profile install keeps only the graphs whose agents and skills it actually installs."""
    from agflow.graph.schema import GraphError, load_graph

    graphs_dir = target / "graphs"
    if not graphs_dir.is_dir():
        return
    agents, skills = set(selection["agents"]), set(selection["skills"])
    for f in sorted(graphs_dir.glob("*.y*ml")):
        try:
            g = load_graph(f)
        except GraphError:
            continue
        missing = sorted({n.agent for n in g.nodes if n.agent and n.agent not in agents}
                         | {n.skill for n in g.nodes if n.skill and n.skill not in skills})
        if missing:
            f.unlink()
            plan.actions.append(f"skip     graph {g.id} (needs {', '.join(missing)})")


def _prune_agent_rosters(agents_dir: Path) -> None:
    """Drop `agents:` entries that point at subagents a profile didn't install."""
    if not agents_dir.is_dir():
        return
    for f in agents_dir.glob("*.md"):
        text = f.read_text(encoding="utf-8")
        meta, _ = frontmatter.parse(text)
        roster = meta.get("agents")
        if not isinstance(roster, list):
            continue
        kept = [a for a in roster if (agents_dir / Path(str(a)).name).exists()
                or (agents_dir / str(a) / "agent.md").exists()]
        if kept != roster:
            atomic_write_text(f, frontmatter.update(text, {"agents": kept}))


def _wire_commands(target: Path, with_hooks: bool, plan: Plan) -> None:
    cmd = agflow_command()
    mcp_file = target / "mcp_config.json"
    if mcp_file.is_file():
        data = read_json(mcp_file, {}) or {}
        server = data.setdefault("mcpServers", {}).setdefault("agflow", {})
        server["command"] = cmd[0]
        server["args"] = [*cmd[1:], "mcp", "serve"]
        write_json(mcp_file, data)
        plan.actions.append(f"wire     MCP server -> {format_command([*cmd, 'mcp', 'serve'])}")
    hooks_file = target / "hooks.json"
    if not with_hooks:
        hooks_file.unlink(missing_ok=True)
        plan.actions.append("skip     hooks (--no-hooks)")
        return
    if hooks_file.is_file():
        data = read_json(hooks_file, {}) or {}
        for entries in (data.get("hooks") or {}).values():
            for entry in entries:
                parts = shlex.split(entry.get("command", ""))
                if parts and parts[0] == "agflow":
                    entry["command"] = format_command([*cmd, *parts[1:]])
        write_json(hooks_file, data)
        plan.actions.append(f"wire     hooks -> {format_command([*cmd, 'hook'])} <event>")


def uninstall(scope: Scope, workspace: Path | None = None, *, dry_run: bool = False) -> Plan:
    manifest_file = _manifest_path(scope, workspace)
    manifest = read_json(manifest_file, None)
    if not manifest:
        raise InstallError(f"no agflow install found for scope {scope} ({manifest_file})")
    plan = Plan(scope=scope, target=_plugin_target(scope, workspace), manifest=manifest_file)
    for p in manifest.get("managed_blocks", []):
        path = Path(p)
        plan.actions.append(f"unmerge  {path}")
        if not dry_run and path.exists():
            text = _BLOCK_RE.sub("", path.read_text(encoding="utf-8")).rstrip()
            atomic_write_text(path, text + "\n" if text else "")
    managed = set(manifest.get("managed_blocks", []))
    for p in manifest.get("created", []):
        path = Path(p)
        if p in managed:
            if path.exists() and not path.read_text(encoding="utf-8").strip() and not dry_run:
                path.unlink()
            continue
        plan.actions.append(f"remove   {path}")
        if not dry_run and (path.exists() or path.is_symlink()):
            remove(path)
    for p, saved in manifest.get("modified", {}).items():
        path, saved_path = Path(p), Path(saved)
        if p in managed:
            continue  # the user's own content was preserved in place
        plan.actions.append(f"restore  {path} <- {saved_path}")
        if not dry_run:
            if path.exists() or path.is_symlink():
                remove(path)
            if saved_path.is_dir():
                shutil.copytree(saved_path, path, symlinks=True)
            elif saved_path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(saved_path, path)
    fallback = _plugin_target(scope, workspace) / "rules" / MASTER_RULE
    if not dry_run:
        fallback.unlink(missing_ok=True)
        manifest_file.unlink(missing_ok=True)
    plan.actions.append(f"remove   {manifest_file}")
    return plan


def status(workspace: Path | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for scope in ("global", "workspace"):
        if scope == "workspace" and workspace is None:
            continue
        m = read_json(_manifest_path(scope, workspace), None)  # type: ignore[arg-type]
        out[scope] = {"installed": bool(m), "target": str(_plugin_target(scope, workspace)),  # type: ignore[arg-type]
                      **({"profile": m.get("profile"), "source": m.get("source")} if m else {})}
    return out


def manifest_json(scope: Scope, workspace: Path | None) -> str:
    return json.dumps(read_json(_manifest_path(scope, workspace), {}), indent=2)
