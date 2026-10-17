"""agflow command-line interface."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from agflow import __version__, frontmatter, paths, platform_vocab
from agflow.fsutil import atomic_write_text, read_yaml
from agflow.inventory import Inventory
from agflow.models import Kind, Level
from agflow.validate import summarize, validate_inventory

app = typer.Typer(no_args_is_help=True, add_completion=False, rich_markup_mode="rich",
                  help="[bold]agflow[/] — Senior Workflows Manager for Google Antigravity "
                       "(skills, agents, rules, MCP, hooks, plugins, graph orchestration).")
skills_app = typer.Typer(no_args_is_help=True, help="Manage skills.")
agents_app = typer.Typer(no_args_is_help=True, help="Manage custom agents / subagents.")
rules_app = typer.Typer(no_args_is_help=True, help="Manage rules (and GEMINI.md).")
mcp_app = typer.Typer(no_args_is_help=True, help="Manage MCP servers; `agflow mcp serve` runs the orchestrator.")
profile_app = typer.Typer(no_args_is_help=True, help="Workflow profiles (bundles of skills/agents/MCP).")
graph_app = typer.Typer(no_args_is_help=True, help="Graph orchestration: validate, render, compile, run.")
sync_app = typer.Typer(no_args_is_help=True, help="Share skills/MCP with other tools.")
for sub, name in ((skills_app, "skills"), (agents_app, "agents"), (rules_app, "rules"), (mcp_app, "mcp"),
                  (profile_app, "profile"), (graph_app, "graph"), (sync_app, "sync")):
    app.add_typer(sub, name=name)

# soft_wrap: never insert hard line breaks into paths (they broke copy-paste of Windows paths)
console = Console(soft_wrap=True)
err = Console(stderr=True, soft_wrap=True)

WorkspaceOpt = typer.Option(None, "--workspace", "-w", help="Workspace root (default: auto-detect from cwd).")


def ws_of(workspace: Path | None) -> Path:
    return (workspace.expanduser().resolve() if workspace else paths.find_workspace())


def _ws_if_any(workspace: Path | None) -> Path | None:
    """The workspace for duplicate detection during a global install (only if one is recognisable)."""
    ws = ws_of(workspace)
    return ws if (ws / ".agents").is_dir() else None


def fail(msg: str, code: int = 1) -> None:
    err.print(f"[red]error:[/] {msg}")
    raise typer.Exit(code)


def print_json(data: Any) -> None:
    sys.stdout.write(json.dumps(data, indent=2, default=str) + "\n")


# --- top level --------------------------------------------------------------------------------


@app.command()
def version() -> None:
    """Print the agflow version."""
    console.print(f"agflow {__version__}")


@app.command()
def doctor(
    workspace: Path | None = WorkspaceOpt,
    path: Path | None = typer.Option(None, "--path", help="Validate a plugin/skills directory instead."),
    source: list[str] = typer.Option([], "--source", "-s",
                                     help="Only issues from this source, e.g. plugin:agflow, global, workspace "
                                          "(repeatable)."),
    show_info: bool = typer.Option(False, "--info", help="Also list INFO-level notes (hidden by default)."),
    strict: bool = typer.Option(False, "--strict", help="Exit non-zero on warnings too."),
    as_json: bool = typer.Option(False, "--json"),
    no_global: bool = typer.Option(False, "--no-global", help="Skip ~/.gemini."),
    fix: bool = typer.Option(False, "--fix", help="Then repair what can be repaired: shows each fix, asks before "
                                                  "applying, lets you choose where a choice is needed, and re-checks "
                                                  "(`agflow fix --undo` reverts)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="With --fix: apply the safe fixes without asking (fixes "
                                                        "that need a choice are still asked, or skipped when not "
                                                        "interactive)."),
) -> None:
    """Validate every skill, agent, rule, GEMINI.md, MCP config, hook, plugin and graph."""
    from rich.markup import escape

    from agflow.validate import by_source

    if path is not None:
        path = path.resolve()
        inv = Inventory.discover(None, include_global=False, include_plugins=False, include_legacy=False,
                                 extra_layouts=[paths.plugin_layout(path)])
    else:
        inv = Inventory.discover(ws_of(workspace), include_global=not no_global)
    all_issues = validate_inventory(inv, probe_hooks=True)  # also runs agflow's installed hook commands once
    issues = [i for i in all_issues if not source or i.source in source]
    counts = summarize(issues)
    sources = by_source(issues)
    if as_json:
        print_json({"counts": counts, "by_source": sources, "issues": [i.to_dict() for i in issues],
                    "items": {k.value: len(inv.of(k)) for k in Kind}})
    else:
        tbl = Table(title="Inventory", show_header=True, header_style="bold")
        for col in ("kind", "enabled", "disabled"):
            tbl.add_column(col)
        for k in Kind:
            on, off = len(inv.of(k)), len(inv.of(k, enabled=False))
            if on or off:
                tbl.add_row(k.value, str(on), str(off))
        console.print(tbl)
        style = {Level.ERROR: "red", Level.WARNING: "yellow", Level.INFO: "cyan"}
        for i in issues:
            if i.level == Level.INFO and not show_info:
                continue
            where = f" [dim]{escape(str(i.path))}[/]" if i.path else ""
            console.print(f"[{style[i.level]}]{i.level.value.upper():7}[/] [dim]({escape(i.source)})[/] "
                          f"{escape(i.code)}: {escape(i.message)}{where}")
        if len(sources) > 1:
            st = Table(title="Issues by source", show_header=True, header_style="bold")
            for col in ("source", "errors", "warnings", "info"):
                st.add_column(col)
            for name, c in sorted(sources.items(), key=lambda kv: (-kv[1]["error"], -kv[1]["warning"], kv[0])):
                st.add_row(name, str(c["error"]), str(c["warning"]), str(c["info"]))
            console.print(st)
        hidden = "" if show_info or not counts["info"] else " (use --info to list them)"
        console.print(f"\n[bold]{counts['error']} error(s), {counts['warning']} warning(s), "
                      f"{counts['info']} info[/]{hidden}")
    if fix:
        if path is not None or as_json:
            fail("--fix works on the installed setup (not with --path or --json); use `agflow fix --json` instead")
        result = _interactive_fix(ws_of(workspace), yes=yes)
        if result is not None:
            issues = [i for i in validate_inventory(Inventory.discover(ws_of(workspace), include_global=not no_global),
                                                    probe_hooks=True)
                      if not source or i.source in source]
            counts = summarize(issues)
    if counts["error"] or (strict and counts["warning"]):
        raise typer.Exit(1)


# --- fix ----------------------------------------------------------------------------------------


def _print_plan(plan: Any, *, show_manual: bool = True) -> None:
    from rich.markup import escape

    safe = [f for f in plan.fixes if f.tier == "safe"]
    review = [f for f in plan.fixes if f.tier == "review"]
    lvl = {"error": "red", "warning": "yellow", "info": "cyan"}
    if safe:
        console.print(f"\n[bold green]Safe fixes ({len(safe)})[/] "
                      "[dim]— mechanical, applied by `agflow fix --apply`[/]")
        for f in safe:
            console.print(f"  [{lvl[f.level]}]●[/] {escape(f.title)} [dim]{f.id} · {escape(f.code)}[/]")
            for change in f.options[0].changes:
                console.print(f"      [dim]- {escape(change)}[/]")
    if review:
        console.print(f"\n[bold yellow]Needs your choice ({len(review)})[/] [dim]— applied only when chosen: "
                      "`--all` (defaults) or `--only ID --option ID=OPTION`[/]")
        for f in review:
            console.print(f"  [{lvl[f.level]}]●[/] {escape(f.title)} [dim]{f.id} · {escape(f.code)}[/]")
            if f.path:
                console.print(f"      [dim]{escape(f.path)}[/]")
            if f.note:
                console.print(f"      {escape(f.note)}")
            for opt in f.options:
                mark = " [green](default)[/]" if opt.id == f.default else ""
                console.print(f"      [bold]{escape(opt.id)}[/]: {escape(opt.label)}{mark}")
                for change in opt.changes:
                    console.print(f"        [dim]- {escape(change)}[/]")
    if show_manual and plan.manual:
        console.print(f"\n[bold]Manual ({len(plan.manual)})[/] [dim]— agflow won't change these automatically[/]")
        for m in plan.manual:
            console.print(f"  [{lvl[m.level]}]●[/] {escape(m.title)} [dim]({escape(m.source)})[/]")
            for step in m.steps:
                console.print(f"      → {escape(step)}")
    if not plan.fixes and not plan.manual:
        console.print("[green]Nothing to fix.[/]")


def _print_result(result: Any) -> None:
    from rich.markup import escape

    for a in result.applied:
        console.print(f"[green]fixed[/]   {escape(a['title'])} [dim]({escape(a['option'])})[/]")
    for a in result.failed:
        console.print(f"[red]failed[/]  {escape(a['title'])}: {escape(a['error'])}")
    for a in result.skipped:
        console.print(f"[yellow]skipped[/] {escape(a['id'])}: {escape(a['reason'])}")
    for a in result.unresolved:
        console.print(f"[yellow]check[/]   {escape(a['title'])}: the issue is still reported after the fix")
    b, a = result.before, result.after
    console.print(f"\n[bold]errors {b.get('error', 0)} → {a.get('error', 0)}, "
                  f"warnings {b.get('warning', 0)} → {a.get('warning', 0)}[/]")
    if result.backup_dir:
        console.print(f"[dim]Backup: {escape(result.backup_dir)} — undo with `agflow fix --undo`[/]")


def _interactive_fix(workspace: Path, *, yes: bool) -> Any:
    """doctor --fix: safe fixes after one confirmation, then one question per fix that needs a choice."""
    from rich.markup import escape

    from agflow import fixes

    plan = fixes.build_plan(workspace, probe_hooks=True)
    console.rule("[bold]Fix")
    _print_plan(plan, show_manual=True)
    if not plan.fixes:
        return None
    selections: dict[str, str | None] = {}
    safe = [f for f in plan.fixes if f.tier == "safe"]
    interactive = sys.stdin.isatty() or not yes
    if safe and (yes or typer.confirm(f"\nApply the {len(safe)} safe fix(es)?", default=True)):
        selections.update({f.id: None for f in safe})
    for f in (f for f in plan.fixes if f.tier == "review"):
        if not interactive:
            console.print(f"[dim]skipped (needs a choice): {escape(f.title)}[/]")
            continue
        console.print(f"\n[bold]{escape(f.title)}[/]" + (f"\n{escape(f.note)}" if f.note else ""))
        for n, opt in enumerate(f.options, start=1):
            console.print(f"  {n}) {escape(opt.label)}")
        console.print("  s) skip")
        valid = [str(n) for n in range(1, len(f.options) + 1)] + ["s"]
        answer = typer.prompt("Choose", default="s").strip().lower()
        while answer not in valid:
            answer = typer.prompt(f"Choose one of {', '.join(valid)}", default="s").strip().lower()
        if answer != "s":
            selections[f.id] = f.options[int(answer) - 1].id
    if not selections:
        console.print("Nothing applied.")
        return None
    result = fixes.apply(workspace, selections, plan=plan)
    console.print()
    _print_result(result)
    return result


@app.command("fix")
def fix_cmd(
    workspace: Path | None = WorkspaceOpt,
    apply_: bool = typer.Option(False, "--apply", help="Apply the safe fixes."),
    all_: bool = typer.Option(False, "--all", help="With --apply: also apply fixes that need a choice, using each "
                                                  "one's default option."),
    only: list[str] = typer.Option([], "--only", help="Apply just this fix ID (repeatable; implies --apply)."),
    option: list[str] = typer.Option([], "--option", help="Choose an option: ID=OPTION (repeatable)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask for confirmation."),
    undo: bool = typer.Option(False, "--undo", help="Revert the most recent fix batch (restores every file)."),
    show_history: bool = typer.Option(False, "--history", help="List previous fix batches."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Preview and apply repairs for doctor's issues. Nothing changes without --apply/--only; every change is
    backed up and `agflow fix --undo` restores it."""
    from rich.markup import escape

    from agflow import fixes

    if undo:
        if not yes and not as_json and not typer.confirm("Revert the most recent fix batch?", default=True):
            raise typer.Exit(1)
        try:
            out = fixes.undo_last()
        except fixes.FixError as exc:
            fail(str(exc))
        if as_json:
            print_json(out)
        else:
            console.print(f"[green]Reverted[/] {len(out['fixes'])} fix(es) from batch {out['id']}: "
                          f"{len(out['restored'])} path(s) restored, {len(out['removed'])} removed.")
            for t in out["fixes"]:
                console.print(f"  [dim]- {escape(t)}[/]")
        return
    if show_history:
        hist = fixes.history()
        if as_json:
            print_json(hist)
            return
        if not hist:
            console.print("No fixes applied yet.")
        for h in hist:
            where = escape(str(h.get("workspace") or ""))
            console.print(f"[bold]{h['id']}[/] {len(h['fixes'])} fix(es) [dim]{where}[/]")
            for t in h["fixes"]:
                console.print(f"  [dim]- {escape(t)}[/]")
        return

    ws = ws_of(workspace)
    plan = fixes.build_plan(ws, probe_hooks=True)
    options: dict[str, str] = {}
    for item in option:
        fid, sep, opt = item.partition("=")
        if not sep or not fid or not opt:
            fail(f"--option expects ID=OPTION, got '{item}'")
        options[fid.strip()] = opt.strip()
    unknown = [i for i in [*only, *options] if plan.get(i) is None]
    if unknown:
        fail(f"unknown fix id(s): {', '.join(unknown)} (run `agflow fix` to list them)")
    for fid, opt in options.items():
        try:
            plan.get(fid).option(opt)  # type: ignore[union-attr]
        except fixes.FixError as exc:
            fail(str(exc))

    if not (apply_ or only):
        if as_json:
            print_json(plan.to_dict())
            return
        c = plan.to_dict()["counts"]
        console.print(f"[bold]{c['error']} error(s), {c['warning']} warning(s)[/] in {escape(str(ws))}")
        _print_plan(plan)
        if plan.fixes:
            console.print("\n[dim]Apply: `agflow fix --apply` (safe) · `agflow fix --apply --all` (all, defaults) · "
                          "`agflow doctor --fix` (step by step) · revert: `agflow fix --undo`[/]")
        return

    selections = fixes.select(plan, include_review=all_, only=only or None, options=options)
    selections.update({fid: opt for fid, opt in options.items() if fid in selections or not only})
    if not selections:
        if as_json:
            print_json(fixes.ApplyResult(before=plan.to_dict()["counts"]).to_dict())
        else:
            review = sum(1 for f in plan.fixes if f.tier == "review")
            console.print("No safe fixes to apply." + (f" {review} fix(es) need a choice: add --all, or --only ID."
                                                       if review else ""))
        return
    if not as_json:
        for fid, opt in selections.items():
            f = plan.get(fid)
            console.print(f"[bold]{escape(f.title)}[/] [dim]({escape(f.option(opt).label)})[/]")  # type: ignore[union-attr]
            for change in f.option(opt).changes:  # type: ignore[union-attr]
                console.print(f"  [dim]- {escape(change)}[/]")
    if not yes and not as_json and not typer.confirm(f"\nApply {len(selections)} fix(es)?", default=True):
        raise typer.Exit(1)
    result = fixes.apply(ws, selections, plan=plan)
    if as_json:
        print_json(result.to_dict())
    else:
        console.print()
        _print_result(result)
    if result.failed:
        raise typer.Exit(1)


@app.command("gemini-md")
def gemini_md(
    file: Path | None = typer.Argument(None, help="File to analyze (default: ~/.gemini/GEMINI.md)."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show where the size of GEMINI.md goes and what to trim (read-only; never edits the file)."""
    from agflow.gemini_md import analyze

    target = (file or paths.global_layout().gemini_md).expanduser()  # type: ignore[union-attr]
    if not target.is_file():
        fail(f"{target} does not exist")
    report = analyze(target.read_text(encoding="utf-8"))
    if as_json:
        print_json({"path": str(target), **report})
        return
    limit = report["limit"]
    state = "[red]over the limit[/]" if report["over_by"] > 0 else "[green]within the limit[/]"
    console.print(f"[bold]{target}[/]: {report['chars']:,} / {limit:,} chars — {state}")
    if report["blocks"]:
        tbl = Table(title="Blocks managed by tools", show_header=True, header_style="bold")
        for col in ("block", "chars", "note"):
            tbl.add_column(col)
        for b in report["blocks"]:
            tbl.add_row(b["name"], f"{b['chars']:,}", b["note"])
        console.print(tbl)
    tbl = Table(title="Sections (largest first)", show_header=True, header_style="bold")
    for col in ("section", "line", "chars", "share"):
        tbl.add_column(col)
    for sec in report["sections"][:25]:
        tbl.add_row(sec["title"], str(sec["line"]), f"{sec['chars']:,}", f"{sec['share']:.0%}")
    console.print(tbl)
    for tip in report["advice"]:
        console.print(f"• {tip}")


@app.command("list")
def list_items(
    kind: Kind | None = typer.Argument(None, help="Only this kind."),
    workspace: Path | None = WorkspaceOpt,
    as_json: bool = typer.Option(False, "--json"),
    all_: bool = typer.Option(False, "--all", help="Include disabled items."),
) -> None:
    """List discovered components across all scopes (precedence order)."""
    inv = Inventory.discover(ws_of(workspace))
    items = [i for i in inv.items if (kind is None or i.kind == kind) and (all_ or i.enabled)]
    if as_json:
        print_json([i.to_dict() for i in items])
        return
    tbl = Table(show_header=True, header_style="bold")
    for col in ("kind", "name", "scope", "on", "description"):
        tbl.add_column(col, overflow="fold")
    for i in items:
        tbl.add_row(i.kind.value, i.name, i.scope, "✓" if i.enabled else "✗", (i.description or "")[:90])
    console.print(tbl)


@app.command()
def init(
    workspace: Path | None = WorkspaceOpt,
    install_plugin: bool = typer.Option(True, "--install/--no-install", help="Also install the plugin here."),
    profile: str | None = typer.Option(None, "--profile", "-p"),
) -> None:
    """Scaffold .agents/ in a workspace (rules, skills, agents, graphs, profiles) and install agflow."""
    ws = ws_of(workspace)
    lay = paths.workspace_layout(ws)
    for d in (lay.rules_dir, lay.skills_dir, lay.agents_dir, lay.graphs_dir):
        d.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
    state = paths.workspace_state_dir(ws)
    gi = state / ".gitignore"
    if not gi.exists():
        atomic_write_text(gi, "runs/\ndisabled/\ninstall.json\n")
    prof = state / "profiles.yaml"
    if not prof.exists():
        atomic_write_text(prof, "# Workspace profiles extend/override the bundled ones (see `agflow profile list`).\n"
                                "profiles: {}\n")
    console.print(f"[green]✓[/] scaffolded {lay.root}")
    if install_plugin:
        from agflow.install import installed_copies

        if "global" in installed_copies(ws):
            console.print("  agflow is installed globally, so it already serves this workspace; not adding a "
                          "second copy here (use `agflow profile apply <name>` to limit it).")
            return
        install(workspace=ws, global_=False, profile=profile, no_hooks=False, no_gemini_md=False, link=False,
                dry_run=False, allow_duplicate=False)


@app.command()
def install(
    workspace: Path | None = WorkspaceOpt,
    global_: bool = typer.Option(True, "--global/--workspace", help="Install to ~/.gemini (default) or workspace."),
    profile: str | None = typer.Option(None, "--profile", "-p", help="Install only a profile's skills/agents."),
    no_hooks: bool = typer.Option(False, "--no-hooks", help="Don't install the guard/ledger/stop hooks."),
    no_gemini_md: bool = typer.Option(False, "--no-gemini-md", help="Don't install the master prompt."),
    link: bool = typer.Option(False, "--link", help="Symlink the plugin (for plugin development)."),
    dry_run: bool = typer.Option(False, "--dry-run"),
    allow_duplicate: bool = typer.Option(False, "--allow-duplicate",
                                         help="Install even if agflow is already installed in the other scope."),
) -> None:
    """Install the agflow plugin (master prompt, rules, skills, agents, hooks, orchestrator MCP)."""
    from agflow.install import InstallError
    from agflow.install import install as do_install
    from agflow.profiles import ProfileError

    scope = "global" if global_ else "workspace"
    try:
        plan = do_install(scope, ws_of(workspace) if not global_ else _ws_if_any(workspace), profile=profile,
                          with_hooks=not no_hooks, with_gemini_md=not no_gemini_md, link=link, dry_run=dry_run,
                          allow_duplicate=allow_duplicate)
    except (InstallError, ProfileError) as exc:
        fail(str(exc))
    for a in plan.actions:
        console.print(f"  {a}", markup=False, highlight=False)
    for w in plan.warnings:
        console.print(f"[yellow]warning:[/] {w}")
    verb = "would install" if dry_run else "installed"
    console.print(f"[green]✓[/] {verb} agflow ({scope}) -> {plan.target}")
    if not dry_run:
        from rich.markup import escape

        from agflow.install import format_command

        console.print("  Restart Antigravity (or reload the window) to pick up the plugin. "
                      "For the agy CLI you can also run: [bold]"
                      + escape(format_command(["agy", "plugin", "install", str(plan.target)])) + "[/]")


@app.command()
def uninstall(
    workspace: Path | None = WorkspaceOpt,
    global_: bool = typer.Option(True, "--global/--workspace"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Remove agflow and restore everything it overwrote."""
    from agflow.install import InstallError
    from agflow.install import uninstall as do_uninstall

    try:
        plan = do_uninstall("global" if global_ else "workspace", None if global_ else ws_of(workspace),
                            dry_run=dry_run)
    except InstallError as exc:
        fail(str(exc))
    for a in plan.actions:
        console.print(f"  {a}", markup=False, highlight=False)
    console.print(f"[green]✓[/] {'would uninstall' if dry_run else 'uninstalled'} agflow")


@app.command()
def status(workspace: Path | None = WorkspaceOpt, as_json: bool = typer.Option(False, "--json")) -> None:
    """Install status and active orchestration runs."""
    from agflow.graph import engine
    from agflow.graph.store import RunStore
    from agflow.install import status as install_status

    ws = ws_of(workspace)
    data = {"workspace": str(ws), "install": install_status(ws),
            "active_runs": [{"run_id": r.run_id, "graph": r.graph_id, "counts": engine.counts(r)}
                            for r in RunStore(ws).active()]}
    if as_json:
        print_json(data)
        return
    console.print(f"[bold]workspace[/] {ws}")
    for scope, info in data["install"].items():
        mark = "[green]installed[/]" if info["installed"] else "[dim]not installed[/]"
        console.print(f"  {scope:9} {mark} {info['target']}"
                      + (f" (profile: {info['profile']})" if info.get("profile") else ""))
    if data["active_runs"]:
        console.print("[bold]active runs[/]")
        for r in data["active_runs"]:
            console.print(f"  {r['run_id']}  {r['counts']}")


@app.command()
def migrate(
    workspace: Path | None = WorkspaceOpt,
    dry_run: bool = typer.Option(False, "--dry-run"),
    no_global: bool = typer.Option(False, "--no-global"),
) -> None:
    """Convert workflows to skills (retired 2026-11-01) and move 1.x layouts to 2.x; `agflow fix --undo` reverts it."""
    from agflow import fixes
    from agflow.migrate import migrate_all

    if dry_run:
        report = migrate_all(ws_of(workspace), include_global=not no_global, dry_run=True)
    else:
        report = fixes.migrate_undoable(ws_of(workspace), include_global=not no_global)
    for a in report.actions:
        console.print(f"  {a}", markup=False, highlight=False)
    for c in report.conflicts:
        console.print(f"[yellow]conflict:[/] {c}")
    if not report.actions and not report.conflicts:
        console.print("[green]✓[/] nothing to migrate")
    elif report.actions and not dry_run:
        console.print("[dim]Undo with `agflow fix --undo`.[/]")


@app.command()
def hook(event: str = typer.Argument(..., help="pre-tool-use | post-tool-use | stop")) -> None:
    """Hook entry point used by hooks.json (reads JSON on stdin, writes a JSON decision)."""
    from agflow.hooks import HANDLERS
    from agflow.hooks import main as hook_main

    if event not in HANDLERS:
        fail(f"unknown hook event {event!r}; valid: {', '.join(HANDLERS)}")
    raise typer.Exit(hook_main(event))  # same process contract as the `python -m agflow hook` fast path


@app.command()
def dashboard(
    workspace: Path | None = WorkspaceOpt,
    port: int = typer.Option(8765, "--port"),
    host: str = typer.Option("127.0.0.1", "--host", help="Keep 127.0.0.1 unless you know why."),
    no_browser: bool = typer.Option(False, "--no-browser"),
) -> None:
    """Open the local manager dashboard (token-protected, localhost only)."""
    from agflow.dashboard.app import serve

    serve(ws_of(workspace), host=host, port=port, open_browser=not no_browser)


# --- skills / agents / rules -------------------------------------------------------------------------

SKILL_TEMPLATE = """# {title}

## When to use
- <situations that should trigger this skill>

## Procedure
1. <first step — be concrete: commands, files, checks>
2. <next step>

## Checklist
- [ ] <verifiable outcome>

## Exit criteria
Report what you did and the evidence (commands run and their results).
"""

AGENT_TEMPLATE = """# {title}

You are the {title}. <one-paragraph role and standards>

## Operating procedure
1. <steps>

## Output contract
End with the JSON result block requested by the task (status, summary, artifacts, followups).
"""


def _kind_list(kind: Kind, workspace: Path | None, as_json: bool) -> None:
    inv = Inventory.discover(ws_of(workspace))
    items = inv.of(kind, enabled=None)
    if as_json:
        print_json([i.to_dict() for i in items])
        return
    tbl = Table(show_header=True, header_style="bold")
    for col in ("name", "scope", "on", "description"):
        tbl.add_column(col, overflow="fold")
    for i in items:
        tbl.add_row(i.name, i.scope, "✓" if i.enabled else "✗", (i.description or "")[:100])
    console.print(tbl)


def _toggle(kind: Kind, name: str, enable: bool, workspace: Path | None) -> None:
    from agflow import toggles

    inv = Inventory.discover(ws_of(workspace))
    try:
        where = toggles.enable(inv, kind, name) if enable else toggles.disable(inv, kind, name)
    except toggles.ToggleError as exc:
        fail(str(exc))
    console.print(f"[green]✓[/] {'enabled' if enable else 'disabled'} {kind.value} {name} ({where})")


def _scope_dir(scope: str, workspace: Path | None, attr: str) -> Path:
    lay = paths.global_layout() if scope == "global" else paths.workspace_layout(ws_of(workspace))
    d = getattr(lay, attr)
    if d is None:
        fail(f"{attr} is not available in the {scope} scope")
    return d


@skills_app.command("list")
def skills_list(workspace: Path | None = WorkspaceOpt, as_json: bool = typer.Option(False, "--json")) -> None:
    """List skills."""
    _kind_list(Kind.SKILL, workspace, as_json)


@skills_app.command("new")
def skills_new(
    name: str,
    description: str = typer.Option(..., "--description", "-d", help="What it does AND when to use it."),
    scope: str = typer.Option("workspace", "--scope", help="workspace | global"),
    workspace: Path | None = WorkspaceOpt,
) -> None:
    """Scaffold a new skill (Agent Skills standard)."""
    from agflow.validate import NAME_RE

    if not NAME_RE.match(name):
        fail("skill names are lowercase-hyphenated, e.g. api-contract-check")
    target = _scope_dir(scope, workspace, "skills_dir") / name / "SKILL.md"
    if target.exists():
        fail(f"{target} already exists")
    atomic_write_text(target, frontmatter.dump({"name": name, "description": description},
                                               SKILL_TEMPLATE.format(title=name.replace("-", " ").title())))
    console.print(f"[green]✓[/] created {target}")


@skills_app.command("enable")
def skills_enable(name: str, workspace: Path | None = WorkspaceOpt) -> None:
    """Re-enable a disabled skill."""
    _toggle(Kind.SKILL, name, True, workspace)


@skills_app.command("disable")
def skills_disable(name: str, workspace: Path | None = WorkspaceOpt) -> None:
    """Disable a skill (parks it outside Antigravity's discovery paths)."""
    _toggle(Kind.SKILL, name, False, workspace)


@agents_app.command("list")
def agents_list(workspace: Path | None = WorkspaceOpt, as_json: bool = typer.Option(False, "--json")) -> None:
    """List custom agents."""
    _kind_list(Kind.AGENT, workspace, as_json)


@agents_app.command("new")
def agents_new(
    name: str,
    description: str = typer.Option(..., "--description", "-d"),
    model: str = typer.Option(platform_vocab.builtin().model_inherit, "--model",
                              help=" | ".join(platform_vocab.builtin().agent_models)),
    tools: str = typer.Option(",".join(platform_vocab.builtin().default_agent_tools), "--tools",
                              help="Comma-separated tool names."),
    main: bool = typer.Option(False, "--main", help="Selectable as the primary agent."),
    scope: str = typer.Option("workspace", "--scope"),
    workspace: Path | None = WorkspaceOpt,
) -> None:
    """Scaffold a custom agent."""
    target = _scope_dir(scope, workspace, "agents_dir") / f"{name}.md"
    if target.exists():
        fail(f"{target} already exists")
    meta = {"name": name, "description": description, "mainAgent": main, "subagent": True, "model": model,
            "tools": [t.strip() for t in tools.split(",") if t.strip()], "commandExecutionPolicy": "auto"}
    atomic_write_text(target, frontmatter.dump(meta, AGENT_TEMPLATE.format(title=name.replace("-", " ").title())))
    console.print(f"[green]✓[/] created {target}")


@agents_app.command("enable")
def agents_enable(name: str, workspace: Path | None = WorkspaceOpt) -> None:
    """Re-enable a disabled agent."""
    _toggle(Kind.AGENT, name, True, workspace)


@agents_app.command("disable")
def agents_disable(name: str, workspace: Path | None = WorkspaceOpt) -> None:
    """Disable an agent."""
    _toggle(Kind.AGENT, name, False, workspace)


@rules_app.command("list")
def rules_list(workspace: Path | None = WorkspaceOpt, as_json: bool = typer.Option(False, "--json")) -> None:
    """List rules and GEMINI.md files."""
    inv = Inventory.discover(ws_of(workspace))
    items = inv.of(Kind.GEMINI_MD, enabled=None) + inv.of(Kind.RULE, enabled=None)
    if as_json:
        print_json([i.to_dict() for i in items])
        return
    tbl = Table(show_header=True, header_style="bold")
    for col in ("name", "scope", "trigger", "chars", "description"):
        tbl.add_column(col, overflow="fold")
    for i in items:
        chars = len(i.path.read_text(encoding="utf-8")) if i.path.exists() else 0
        tbl.add_row(i.name, i.scope, str(i.meta.get("trigger") or ("always" if i.kind == Kind.GEMINI_MD else "")),
                    f"{chars}/{paths.GEMINI_MD_CHAR_LIMIT}", (i.description or "")[:80])
    console.print(tbl)


@rules_app.command("new")
def rules_new(
    name: str,
    trigger: str = typer.Option("model_decision", "--trigger", help=" | ".join(platform_vocab.builtin().rule_triggers)),
    description: str = typer.Option("", "--description", "-d"),
    globs: str = typer.Option("", "--globs", help="Comma-separated globs (trigger=glob)."),
    workspace: Path | None = WorkspaceOpt,
) -> None:
    """Scaffold a workspace rule with valid trigger frontmatter."""
    triggers = platform_vocab.current().rule_triggers
    if trigger not in triggers:
        fail(f"trigger must be one of {sorted(triggers)}")
    if trigger == "glob" and not globs:
        fail("--globs is required with --trigger glob")
    meta: dict[str, Any] = {"trigger": trigger}
    if description:
        meta["description"] = description
    if globs:
        meta["globs"] = globs
    target = paths.workspace_layout(ws_of(workspace)).rules_dir / f"{name}.md"  # type: ignore[operator]
    if target.exists():
        fail(f"{target} already exists")
    atomic_write_text(target, frontmatter.dump(meta, f"# {name}\n\n- <rule>\n"))
    console.print(f"[green]✓[/] created {target}")


@rules_app.command("enable")
def rules_enable(name: str, workspace: Path | None = WorkspaceOpt) -> None:
    """Re-enable a disabled rule."""
    _toggle(Kind.RULE, name, True, workspace)


@rules_app.command("disable")
def rules_disable(name: str, workspace: Path | None = WorkspaceOpt) -> None:
    """Disable a rule."""
    _toggle(Kind.RULE, name, False, workspace)


# --- MCP ------------------------------------------------------------------------------------------


@mcp_app.command("serve")
def mcp_serve(workspace: Path | None = WorkspaceOpt) -> None:
    """Run the agflow orchestrator MCP server over stdio (Antigravity launches this)."""
    import os

    from agflow.mcp_server import serve

    if workspace:
        os.environ["AGFLOW_WORKSPACE"] = str(workspace.resolve())
    serve()


@mcp_app.command("list")
def mcp_list(
    scope: str = typer.Option("all", "--scope", help="all | global | workspace | agy-cli"),
    workspace: Path | None = WorkspaceOpt,
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List MCP servers."""
    import os

    from agflow import mcp_registry

    inv = Inventory.discover(ws_of(workspace))
    items = [i for i in inv.of(Kind.MCP, enabled=None) if scope == "all" or i.scope == scope]
    if as_json:
        print_json([i.to_dict() for i in items])
        return
    tbl = Table(show_header=True, header_style="bold")
    for col in ("name", "scope", "on", "launch", "missing env"):
        tbl.add_column(col, overflow="fold")
    for i in items:
        missing = mcp_registry.missing_env(i.meta.get("config", {}), dict(os.environ))
        tbl.add_row(i.name, i.scope, "✓" if i.enabled else "✗", i.description[:80], ", ".join(missing))
    console.print(tbl)


@mcp_app.command("catalog")
def mcp_catalog() -> None:
    """Show curated MCP servers you can add by name (`agflow mcp add github`)."""
    from agflow.mcp_registry import CATALOG

    for name, spec in CATALOG.items():
        console.print(f"[bold]{name:20}[/] {spec.get('command')} {' '.join(spec.get('args', []))}")


@mcp_app.command("add")
def mcp_add(
    name: str,
    command: str | None = typer.Option(None, "--command", "-c"),
    arg: list[str] = typer.Option([], "--arg", "-a", help="Repeatable."),
    env: list[str] = typer.Option([], "--env", "-e", help="KEY=VALUE, prefer KEY=${VAR}. Repeatable."),
    url: str | None = typer.Option(None, "--url"),
    scope: str = typer.Option("global", "--scope", help="global | workspace | agy-cli"),
    replace: bool = typer.Option(False, "--replace"),
    workspace: Path | None = WorkspaceOpt,
) -> None:
    """Add an MCP server (by catalog name, --command, or --url)."""
    from agflow import mcp_registry

    env_map = {}
    for e in env:
        if "=" not in e:
            fail(f"--env expects KEY=VALUE, got {e!r}")
        k, v = e.split("=", 1)
        env_map[k] = v
    try:
        f = mcp_registry.config_file(scope, ws_of(workspace))
        spec = mcp_registry.add_server(f, name, command=command, args=arg, env=env_map, url=url, replace=replace)
    except mcp_registry.McpError as exc:
        fail(str(exc))
    console.print(f"[green]✓[/] added {name} to {f}: {json.dumps(spec)}")


def _mcp_simple(action: str, name: str, scope: str, workspace: Path | None) -> None:
    from agflow import mcp_registry

    try:
        f = mcp_registry.config_file(scope, ws_of(workspace))
        if action == "remove":
            mcp_registry.remove_server(f, name)
        else:
            mcp_registry.set_enabled(f, name, action == "enable")
    except mcp_registry.McpError as exc:
        fail(str(exc))
    console.print(f"[green]✓[/] {action}d {name} in {f}")


@mcp_app.command("remove")
def mcp_remove(name: str, scope: str = typer.Option("global", "--scope"),
               workspace: Path | None = WorkspaceOpt) -> None:
    """Remove an MCP server."""
    _mcp_simple("remove", name, scope, workspace)


@mcp_app.command("enable")
def mcp_enable(name: str, scope: str = typer.Option("global", "--scope"),
               workspace: Path | None = WorkspaceOpt) -> None:
    """Enable an MCP server."""
    _mcp_simple("enable", name, scope, workspace)


@mcp_app.command("disable")
def mcp_disable(name: str, scope: str = typer.Option("global", "--scope"),
                workspace: Path | None = WorkspaceOpt) -> None:
    """Disable an MCP server (keeps its config)."""
    _mcp_simple("disable", name, scope, workspace)


# --- profiles -------------------------------------------------------------------------------------


@profile_app.command("list")
def profile_list(workspace: Path | None = WorkspaceOpt) -> None:
    """List available profiles."""
    from agflow.profiles import load_profiles

    for name, spec in sorted(load_profiles(None, ws_of(workspace)).items()):
        console.print(f"[bold]{name:16}[/] {(spec or {}).get('description', '')}")


@profile_app.command("show")
def profile_show(name: str, workspace: Path | None = WorkspaceOpt) -> None:
    """Show the resolved skills/agents/MCP of a profile."""
    from agflow.profiles import ProfileError, resolve_profile

    try:
        print_json(resolve_profile(name, None, ws_of(workspace)))
    except ProfileError as exc:
        fail(str(exc))


@profile_app.command("apply")
def profile_apply(name: str, workspace: Path | None = WorkspaceOpt) -> None:
    """Enable exactly a profile's skills/agents (workspace scope + workspace plugins)."""
    from agflow.profiles import ProfileError, resolve_profile
    from agflow.toggles import ToggleError, apply_profile

    ws = ws_of(workspace)
    try:
        actions = apply_profile(ws, resolve_profile(name, None, ws))
    except (ProfileError, ToggleError) as exc:
        fail(str(exc))
    for a in actions:
        console.print(f"  {a}", markup=False, highlight=False)
    console.print(f"[green]✓[/] profile {name} applied ({len(actions)} change(s))")


# --- sync -----------------------------------------------------------------------------------------


@sync_app.command("gemini-cli")
def sync_gemini_cli(
    workspace: Path | None = WorkspaceOpt,
    project: bool = typer.Option(False, "--project", help="Write to <ws>/.gemini instead of ~/.gemini."),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Copy enabled skills and MCP servers into Gemini CLI's config."""
    from agflow.sync import SyncError
    from agflow.sync import sync_gemini_cli as do_sync

    try:
        report = do_sync(ws_of(workspace), project=project, dry_run=dry_run)
    except SyncError as exc:
        fail(str(exc))
    for note in report.notes:
        console.print(note, style="dim", markup=False, highlight=False)
    for a in report.actions:
        console.print(f"  {a}", markup=False, highlight=False)
    for s in report.skipped:
        console.print(f"[yellow]skipped:[/] {s}")


# --- graphs ---------------------------------------------------------------------------------------


def _parse_inputs(pairs: list[str]) -> dict[str, str]:
    out = {}
    for p in pairs:
        if "=" not in p:
            fail(f"--input expects key=value, got {p!r}")
        k, v = p.split("=", 1)
        out[k.strip()] = v
    return out


@graph_app.command("list")
def graph_list(workspace: Path | None = WorkspaceOpt) -> None:
    """List graphs (workspace > plugins > global > bundled)."""
    from agflow.graph.catalog import list_graphs

    tbl = Table(show_header=True, header_style="bold")
    for col in ("id", "source", "title", "path"):
        tbl.add_column(col, overflow="fold")
    for r in list_graphs(ws_of(workspace)):
        tbl.add_row(r.id + (" [red](invalid)[/]" if r.error else ""), r.source, r.title, str(r.path))
    console.print(tbl)


@graph_app.command("validate")
def graph_validate(files: list[Path] = typer.Argument(..., help="Graph YAML files.")) -> None:
    """Validate graph files (schema, DAG, rework targets, templates)."""
    from agflow.graph.schema import GraphError, load_graph

    bad = 0
    for f in files:
        try:
            g = load_graph(f)
            console.print(f"[green]✓[/] {f} ({g.id}: {len(g.nodes)} nodes)")
        except GraphError as exc:
            bad += 1
            console.print(f"[red]✗[/] {exc}")
    if bad:
        raise typer.Exit(1)


@graph_app.command("render")
def graph_render(
    graph: str,
    run: str | None = typer.Option(None, "--run", help="Colour nodes by this run's state."),
    workspace: Path | None = WorkspaceOpt,
) -> None:
    """Print a graph (or a run) as a Mermaid flowchart."""
    from agflow.graph.catalog import resolve
    from agflow.graph.render import to_mermaid
    from agflow.graph.schema import GraphError
    from agflow.graph.store import RunNotFound, RunStore

    ws = ws_of(workspace)
    try:
        if run:
            state = RunStore(ws).load(run)
            sys.stdout.write(to_mermaid(state.graph_model(), state))
        else:
            sys.stdout.write(to_mermaid(resolve(graph, ws)[0]))
    except (GraphError, RunNotFound) as exc:
        fail(str(exc))


@graph_app.command("compile")
def graph_compile(
    graph: str,
    scope: str = typer.Option("workspace", "--scope", help="workspace | global"),
    workspace: Path | None = WorkspaceOpt,
) -> None:
    """Compile a graph into a SKILL.md so `/<graph-id>` launches it in Antigravity."""
    from agflow.graph.catalog import resolve
    from agflow.graph.compile import compile_to_skill
    from agflow.graph.schema import GraphError

    try:
        g, _ = resolve(graph, ws_of(workspace))
    except GraphError as exc:
        fail(str(exc))
    target = compile_to_skill(g, _scope_dir(scope, workspace, "skills_dir"))
    console.print(f"[green]✓[/] wrote {target} — invoke it with /{g.id}")


def _backend(kind: str, ws: Path, script: Path | None, allow_risky: bool, delay: float) -> Any:
    from agflow.runner.backends import FakeBackend, RunnerError, SdkBackend

    if kind == "fake":
        data = read_yaml(script, {}) if script else {}
        return FakeBackend(data or {}, delay=delay)
    if kind == "sdk":
        try:
            return SdkBackend(ws, allow_risky=allow_risky)
        except RunnerError as exc:
            fail(str(exc))
    fail(f"unknown backend {kind!r} (sdk | fake)")


def _drive(ws: Path, run_id: str, backend: Any, approve_human: bool) -> None:
    from agflow.graph.store import RunStore
    from agflow.runner.runner import exit_code, run_graph

    colors = {"succeeded": "green", "failed": "red", "rejected": "red", "retry": "yellow", "rework": "magenta",
              "dispatched": "cyan", "skipped": "dim", "blocked": "red", "waiting_on_human": "yellow"}

    def show(e: dict[str, Any]) -> None:
        ev = e.get("event", "")
        if ev == "succeeded":
            return  # duplicate of the "reported" line
        name = e.get("status") if ev == "reported" else ev
        detail = e.get("node") or e.get("gate") or ""
        extra = e.get("summary") or e.get("question") or e.get("error") or ""
        console.print(f"[{colors.get(name, 'white')}]{name:>16}[/] {detail} [dim]{str(extra)[:140]}[/]")

    store = RunStore(ws)
    state = asyncio.run(run_graph(store, run_id, backend, approve_human=approve_human, on_event=show))
    report = store.run_dir(run_id) / "report.md"
    code = exit_code(state)
    if code == 3:
        waiting = [n for n, s in state.nodes.items() if s.status.value == "running"]
        console.print(f"[yellow]⏸ waiting on human approval:[/] {', '.join(waiting)} — "
                      f"`agflow graph approve {run_id} <node>` then `agflow graph resume {run_id}`")
    console.print(f"[bold]run {run_id}: {state.status}[/] — report: {report}")
    raise typer.Exit(code)


@graph_app.command("run")
def graph_run(
    graph: str,
    input_: list[str] = typer.Option([], "--input", "-i", help="key=value (repeatable)."),
    backend: str = typer.Option("sdk", "--backend", "-b", help="sdk (Antigravity SDK) | fake (scripted)"),
    script: Path | None = typer.Option(None, "--script", help="YAML results script for the fake backend."),
    approve_human: bool = typer.Option(False, "--approve-human", help="Auto-approve human nodes."),
    allow_risky: bool = typer.Option(False, "--allow-risky", help="SDK: allow commands the guard would 'ask' for."),
    delay: float = typer.Option(0.0, "--delay", help="Fake backend: seconds per node."),
    workspace: Path | None = WorkspaceOpt,
) -> None:
    """Run a graph headlessly (exit 0 succeeded, 1 failed, 3 waiting on a human)."""
    from agflow.graph.catalog import resolve
    from agflow.graph.schema import GraphError, coerce_inputs
    from agflow.graph.store import RunStore

    ws = ws_of(workspace)
    try:
        g, gpath = resolve(graph, ws)
        inputs = coerce_inputs(g, _parse_inputs(input_))
    except GraphError as exc:
        fail(str(exc))
    be = _backend(backend, ws, script, allow_risky, delay)
    state = RunStore(ws).create(g, inputs, graph_path=str(gpath))
    console.print(f"[bold]▶ run {state.run_id}[/] ({g.id}, backend={be.name})")
    _drive(ws, state.run_id, be, approve_human)


@graph_app.command("resume")
def graph_resume(
    run_id: str,
    backend: str = typer.Option("sdk", "--backend", "-b"),
    script: Path | None = typer.Option(None, "--script"),
    approve_human: bool = typer.Option(False, "--approve-human"),
    allow_risky: bool = typer.Option(False, "--allow-risky"),
    workspace: Path | None = WorkspaceOpt,
) -> None:
    """Continue an existing run headlessly."""
    import time

    from agflow.graph import engine
    from agflow.graph.store import RunNotFound, RunStore

    ws = ws_of(workspace)
    store = RunStore(ws)
    try:
        with store.transaction(run_id) as state:
            g = state.graph_model()
            engine.release(g, state, [n for n in state.nodes if g.node(n).type == "agent"], now=time.time())
    except RunNotFound:
        fail(f"unknown run {run_id} in {ws}")
    _drive(ws, run_id, _backend(backend, ws, script, allow_risky, 0.0), approve_human)


@graph_app.command("approve")
def graph_approve(
    run_id: str,
    node: str,
    reject: bool = typer.Option(False, "--reject"),
    note: str = typer.Option("", "--note"),
    workspace: Path | None = WorkspaceOpt,
) -> None:
    """Record a human decision for a waiting approval node."""
    from agflow.graph.engine import EngineError
    from agflow.graph.store import RunNotFound, RunStore
    from agflow.runner.runner import approve

    try:
        approve(RunStore(ws_of(workspace)), run_id, node, approved=not reject, note=note)
    except (EngineError, RunNotFound, KeyError) as exc:
        fail(str(exc))
    console.print(f"[green]✓[/] {'rejected' if reject else 'approved'} {node}")


@graph_app.command("status")
def graph_status(
    run_id: str | None = typer.Argument(None),
    workspace: Path | None = WorkspaceOpt,
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show a run's state (default: latest run)."""
    from agflow.graph import engine
    from agflow.graph.store import RunNotFound, RunStore

    store = RunStore(ws_of(workspace))
    try:
        state = store.load(run_id) if run_id else (store.list() or [None])[0]
    except RunNotFound:
        state = None
    if state is None:
        fail("no such run")
    view = engine.status_view(state.graph_model(), state)
    if as_json:
        print_json(view)
        return
    console.print(f"[bold]{view['run_id']}[/] {view['status']}  {view['counts']}")
    for n in view["nodes"]:
        console.print(f"  {n['status']:>9}  {n['id']:<20} {n.get('agent') or n['type']:<18} "
                      f"[dim]{n.get('summary', '')[:100]}[/]")
    console.print(f"[dim]next:[/] {view['next_action']}")


@graph_app.command("runs")
def graph_runs(workspace: Path | None = WorkspaceOpt) -> None:
    """List runs in the workspace."""
    from agflow.graph import engine
    from agflow.graph.store import RunStore

    for r in RunStore(ws_of(workspace)).list():
        console.print(f"{r.run_id:45} {r.status:10} {engine.counts(r)}")


@graph_app.command("abort")
def graph_abort(run_id: str, reason: str = typer.Option("aborted from CLI", "--reason"),
                workspace: Path | None = WorkspaceOpt) -> None:
    """Abort a run."""
    import time

    from agflow.graph import engine
    from agflow.graph.store import RunNotFound, RunStore

    store = RunStore(ws_of(workspace))
    try:
        with store.transaction(run_id) as state:
            engine.abort(state, reason, now=time.time())
    except RunNotFound:
        fail(f"unknown run {run_id}")
    store.ledger(run_id, {"event": "aborted", "reason": reason})
    console.print(f"[green]✓[/] aborted {run_id}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
