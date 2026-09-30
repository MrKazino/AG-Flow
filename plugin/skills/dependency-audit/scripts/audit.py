#!/usr/bin/env python3
"""Detect package ecosystems under --root and run their vulnerability audit tools (read-only).

Prints a JSON summary: which ecosystems were found, which tools ran, their exit codes and a
vulnerability count when the tool's JSON output could be parsed. Tools that are not installed
are reported as "missing" together with an install hint. Nothing is installed or modified.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

CHECKS = [
    # (ecosystem, marker files, tool, command, install hint)
    ("npm", ["package-lock.json"], "npm", ["npm", "audit", "--json"], "ships with Node.js"),
    ("pnpm", ["pnpm-lock.yaml"], "pnpm", ["pnpm", "audit", "--json"], "npm i -g pnpm"),
    ("yarn", ["yarn.lock"], "yarn", ["yarn", "npm", "audit", "--json"], "corepack enable"),
    ("python", ["requirements.txt", "pyproject.toml", "poetry.lock", "uv.lock", "Pipfile.lock"], "pip-audit",
     ["pip-audit", "-f", "json"], "pipx install pip-audit"),
    ("go", ["go.mod"], "govulncheck", ["govulncheck", "-json", "./..."],
     "go install golang.org/x/vuln/cmd/govulncheck@latest"),
    ("rust", ["Cargo.lock"], "cargo-audit", ["cargo", "audit", "--json"], "cargo install cargo-audit"),
    ("ruby", ["Gemfile.lock"], "bundle-audit", ["bundle-audit", "check", "--update"], "gem install bundler-audit"),
    ("php", ["composer.lock"], "composer", ["composer", "audit", "--format=json"], "https://getcomposer.org"),
]
SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "dist", "build", "target", "vendor", "__pycache__"}


def find_projects(root: Path, markers: list[str], max_depth: int = 4) -> list[Path]:
    found: set[Path] = set()
    for marker in markers:
        for path in root.rglob(marker):
            rel = path.relative_to(root)
            if len(rel.parts) - 1 > max_depth or any(p in SKIP_DIRS for p in rel.parts):
                continue
            found.add(path.parent)
    return sorted(found)


def count_vulns(ecosystem: str, stdout: str) -> int | None:
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        if ecosystem == "go":  # govulncheck streams JSON objects
            return stdout.count('"finding"') or None
        return None
    if ecosystem in ("npm", "pnpm"):
        meta = data.get("metadata", {}).get("vulnerabilities")
        if isinstance(meta, dict):
            return sum(v for k, v in meta.items() if k != "total" and isinstance(v, int))
        return len(data.get("advisories", {}) or data.get("vulnerabilities", {}))
    if ecosystem == "python":
        deps = data.get("dependencies", data) if isinstance(data, dict) else data
        return sum(len(d.get("vulns", [])) for d in deps if isinstance(d, dict))
    if ecosystem == "rust":
        return int(data.get("vulnerabilities", {}).get("count", 0))
    if ecosystem == "php":
        return sum(len(v) for v in (data.get("advisories") or {}).values())
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", type=Path)
    parser.add_argument("--json", action="store_true", help="print JSON (default: human summary)")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    root = args.root.resolve()
    results = []
    for ecosystem, markers, tool, cmd, hint in CHECKS:
        for project in find_projects(root, markers):
            entry = {"ecosystem": ecosystem, "project": str(project.relative_to(root) or "."), "tool": tool}
            if shutil.which(cmd[0]) is None:
                entry.update(status="missing", install_hint=hint)
            else:
                try:
                    proc = subprocess.run(cmd, cwd=project, capture_output=True, text=True, timeout=args.timeout)
                    entry.update(status="ran", exit_code=proc.returncode,
                                 vulnerabilities=count_vulns(ecosystem, proc.stdout),
                                 stderr_tail=proc.stderr[-800:])
                except subprocess.TimeoutExpired:
                    entry.update(status="timeout")
            results.append(entry)
    if args.json:
        print(json.dumps({"root": str(root), "results": results}, indent=2))
    else:
        if not results:
            print("No supported dependency manifests found.")
        for r in results:
            detail = r.get("vulnerabilities", r.get("install_hint", ""))
            print(f"{r['ecosystem']:8} {r['project']:30} {r['tool']:12} {r['status']:8} {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
