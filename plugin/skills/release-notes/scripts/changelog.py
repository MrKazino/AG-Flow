#!/usr/bin/env python3
"""Group commits since a ref into a changelog and suggest the semantic-version bump.

Understands Conventional Commits (`feat(scope)!: subject`, `BREAKING CHANGE:` footers). Read-only.

    python3 changelog.py --since v1.2.0 --markdown
    python3 changelog.py --since v1.2.0 --json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

TYPES = {
    "feat": "Features", "fix": "Fixes", "perf": "Performance", "security": "Security", "refactor": "Refactoring",
    "docs": "Documentation", "test": "Tests", "build": "Build", "ci": "CI", "chore": "Chores", "revert": "Reverts",
    "style": "Style", "deps": "Dependencies",
}
HEADER = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]+)\))?(?P<bang>!)?:\s*(?P<subject>.+)$")
SEP = "\x1e"
FIELD = "\x1f"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def last_tag() -> str | None:
    try:
        return git("describe", "--tags", "--abbrev=0").strip() or None
    except subprocess.CalledProcessError:
        return None


def commits(since: str | None, until: str) -> list[dict]:
    rng = f"{since}..{until}" if since else until
    raw = git("log", rng, "--no-merges", f"--format=%H{FIELD}%s{FIELD}%b{FIELD}%an{SEP}")
    out = []
    for chunk in raw.split(SEP):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        sha, subject, body, author = (chunk.split(FIELD) + ["", "", "", ""])[:4]
        m = HEADER.match(subject.strip())
        breaking = "BREAKING CHANGE" in body or "BREAKING-CHANGE" in body
        entry = {"sha": sha[:10], "author": author.strip(), "subject": subject.strip(), "type": "other",
                 "scope": None, "breaking": breaking}
        if m:
            entry.update(type=m["type"], scope=m["scope"], subject=m["subject"].strip(),
                         breaking=breaking or bool(m["bang"]))
        out.append(entry)
    return out


def suggest_bump(items: list[dict], current: str | None) -> str:
    zero = bool(current and re.match(r"^v?0\.", current))
    if any(i["breaking"] for i in items):
        return "minor" if zero else "major"
    if any(i["type"] == "feat" for i in items):
        return "minor"
    return "patch" if items else "none"


def to_markdown(items: list[dict], since: str | None, bump: str) -> str:
    lines = [f"## Unreleased (since {since or 'the beginning'}) — suggested bump: **{bump}**", ""]
    breaking = [i for i in items if i["breaking"]]
    if breaking:
        lines += ["### ⚠ Breaking changes", ""] + [_line(i) for i in breaking] + [""]
    for key, title in TYPES.items():
        group = [i for i in items if i["type"] == key and not i["breaking"]]
        if group:
            lines += [f"### {title}", ""] + [_line(i) for i in group] + [""]
    other = [i for i in items if i["type"] not in TYPES and not i["breaking"]]
    if other:
        lines += ["### Other changes", ""] + [_line(i) for i in other] + [""]
    return "\n".join(lines).rstrip() + "\n"


def _line(i: dict) -> str:
    scope = f"**{i['scope']}:** " if i["scope"] else ""
    return f"- {scope}{i['subject']} ({i['sha']})"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="start ref (default: last tag)")
    ap.add_argument("--until", default="HEAD")
    fmt = ap.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true")
    fmt.add_argument("--markdown", action="store_true")
    args = ap.parse_args()
    since = args.since or last_tag()
    try:
        items = commits(since, args.until)
    except subprocess.CalledProcessError as exc:
        print(f"git failed: {exc.stderr.strip()}", file=sys.stderr)
        return 2
    bump = suggest_bump(items, since)
    if args.json:
        print(json.dumps({"since": since, "until": args.until, "suggested_bump": bump, "commits": items}, indent=2))
    else:
        print(to_markdown(items, since, bump))
    return 0


if __name__ == "__main__":
    sys.exit(main())
