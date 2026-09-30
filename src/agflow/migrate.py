"""Migrations: workflows -> skills (workflows are retired 2026-11-01), `.agent/` -> `.agents/`,
and Antigravity 1.x global paths -> 2.x."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from agflow import frontmatter, paths
from agflow.fsutil import atomic_write_text, read_json, write_json

_TURBO = re.compile(r"^\s*//\s*turbo(-all)?\s*$", re.MULTILINE)


@dataclass
class MigrationReport:
    actions: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (s or "workflow")[:64].rstrip("-")


def workflow_to_skill(text: str, name: str) -> str:
    meta, body = frontmatter.parse(text)
    desc = str(meta.get("description") or "").strip()
    if not desc:
        first = next((ln.strip("# ").strip() for ln in body.splitlines() if ln.strip()), name)
        desc = first
    desc = desc.rstrip(".")
    description = f"{desc}. Use when the user invokes /{name} or asks for this workflow."[:1024]
    body = _TURBO.sub("<!-- auto-run (turbo) marker removed during migration: skills follow your "
                      "command permission settings. -->", body).strip()
    return frontmatter.dump({"name": name, "description": description}, f"# /{name}\n\n{body}\n")


def migrate_workflows(workspace: Path | None, *, include_global: bool = True, dry_run: bool = False,
                      report: MigrationReport | None = None) -> MigrationReport:
    report = report or MigrationReport()
    jobs: list[tuple[Path, Path]] = []  # (workflow dir, target skills dir)
    if workspace is not None:
        ws_skills = paths.workspace_layout(workspace).skills_dir
        for d in (*paths.workspace_layout(workspace).workflows_dirs,
                  *paths.legacy_workspace_layout(workspace).workflows_dirs):
            jobs.append((d, ws_skills))  # type: ignore[arg-type]
    if include_global:
        g_skills = paths.global_layout().skills_dir
        for d in (*paths.global_layout().workflows_dirs, *paths.legacy_global_layout().workflows_dirs):
            jobs.append((d, g_skills))  # type: ignore[arg-type]
    for wdir, skills_dir in jobs:
        if not wdir.is_dir():
            continue
        for wf in sorted(wdir.glob("*.md")):
            name = slugify(wf.stem)
            target = skills_dir / name / "SKILL.md"
            if target.exists():
                report.conflicts.append(f"skill '{name}' already exists at {target}; skipped {wf}")
                continue
            report.actions.append(f"workflow {wf} -> {target} (original kept as {wf.name}.bak)")
            if not dry_run:
                atomic_write_text(target, workflow_to_skill(wf.read_text(encoding="utf-8"), name))
                wf.rename(wf.with_name(wf.name + ".bak"))
    return report


def migrate_legacy_workspace(workspace: Path, *, dry_run: bool = False,
                             report: MigrationReport | None = None) -> MigrationReport:
    report = report or MigrationReport()
    old, new = paths.legacy_workspace_layout(workspace), paths.workspace_layout(workspace)
    for sub_old, sub_new in ((old.rules_dir, new.rules_dir), (old.skills_dir, new.skills_dir)):
        if not sub_old or not sub_old.is_dir():
            continue
        for entry in sorted(sub_old.iterdir()):
            dest = sub_new / entry.name  # type: ignore[operator]
            if dest.exists():
                report.conflicts.append(f"{dest} exists; left {entry} in place")
                continue
            report.actions.append(f"move     {entry} -> {dest}")
            if not dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(entry), str(dest))
    return report


def migrate_legacy_global(*, dry_run: bool = False, report: MigrationReport | None = None) -> MigrationReport:
    """Copy (never move) 1.x global skills and MCP servers into the 2.x locations."""
    report = report or MigrationReport()
    old, new = paths.legacy_global_layout(), paths.global_layout()
    if old.skills_dir and old.skills_dir.is_dir():
        for entry in sorted(p for p in old.skills_dir.iterdir() if (p / "SKILL.md").is_file()):
            dest = new.skills_dir / entry.name  # type: ignore[operator]
            if dest.exists():
                report.conflicts.append(f"{dest} exists; skipped legacy skill {entry}")
                continue
            report.actions.append(f"copy     {entry} -> {dest}")
            if not dry_run:
                shutil.copytree(entry, dest)
    if old.mcp_file and old.mcp_file.is_file():
        legacy = (read_json(old.mcp_file, {}) or {}).get("mcpServers", {}) or {}
        current = read_json(new.mcp_file, {}) or {}  # type: ignore[arg-type]
        servers = current.setdefault("mcpServers", {})
        added = [n for n in legacy if n not in servers]
        for n in legacy:
            if n in servers:
                report.conflicts.append(f"MCP server '{n}' already in {new.mcp_file}; kept the 2.x entry")
        if added:
            report.actions.append(f"merge    MCP servers {added} -> {new.mcp_file}")
            if not dry_run:
                for n in added:
                    servers[n] = legacy[n]
                write_json(new.mcp_file, current)  # type: ignore[arg-type]
    return report


def migrate_all(workspace: Path | None, *, include_global: bool = True, dry_run: bool = False) -> MigrationReport:
    report = MigrationReport()
    if workspace is not None:
        migrate_legacy_workspace(workspace, dry_run=dry_run, report=report)
    if include_global:
        migrate_legacy_global(dry_run=dry_run, report=report)
    migrate_workflows(workspace, include_global=include_global, dry_run=dry_run, report=report)
    return report
