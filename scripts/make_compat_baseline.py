#!/usr/bin/env python3
"""Refresh the shipped compatibility baseline from the Antigravity installed on this machine.

    python scripts/make_compat_baseline.py            # check, then rewrite 'verified' and 'tested_antigravity'
    python scripts/make_compat_baseline.py --dry-run  # only report

Run it when a release has been verified against a new Antigravity (hooks, plugin and sidecar checks done by hand or by
the live tests). It refuses to write if any requirement in the file does not hold for the installed Antigravity, so a
baseline can never be recorded as verified while a requirement is broken. Read-only for Antigravity; writes only
``src/agflow/data/compat_baseline.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agflow import antigravity_probe as ap  # noqa: E402
from agflow import compat  # noqa: E402


def portable(path: str | None) -> str | None:
    """``path`` relative to the home folder (``~/...``), or ``None``. The baseline is published inside the wheel, so it
    must not carry the name of the machine or the person it was made on."""
    if not path:
        return None
    try:
        return "~/" + Path(path).relative_to(Path.home()).as_posix()
    except ValueError:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    args = parser.parse_args(argv)

    target = compat.BASELINE_FILE
    data = json.loads(target.read_text(encoding="utf-8"))
    snap = ap.probe()
    if not snap.has_docs or not snap.agy_version:
        print("Antigravity (agy and its shipped docs) was not found; nothing to verify against.", file=sys.stderr)
        return 2

    probe_view = dict(data, verified={}, tested_antigravity=[])
    report = compat.check(snap, probe_view)
    broken = report.at_level("breaking")
    if broken:
        print("Refusing to record a baseline: these requirements do not hold for the installed Antigravity:",
              file=sys.stderr)
        for finding in broken:
            print(f"  - {finding.component}: {finding.message}", file=sys.stderr)
        return 1

    data["verified"] = dict(snap.to_json(), skills_root=portable(snap.skills_root))
    data["tested_antigravity"] = sorted({*data.get("tested_antigravity", []), snap.agy_version})
    print(f"Antigravity {snap.agy_version}: {len(data['requires'])} requirements hold; "
          f"{len(snap.doc_hashes)} docs fingerprinted.")
    if args.dry_run:
        return 0
    # requirements stay one per line (reviewable diffs); the rest is compact JSON
    head = {k: v for k, v in data.items() if k != "requires"}
    body = json.dumps(head, indent=2, ensure_ascii=False)[:-2]  # drop the closing "\n}"
    lines = ",\n".join("    " + json.dumps(r, ensure_ascii=False) for r in data["requires"])
    target.write_text(f'{body},\n  "requires": [\n{lines}\n  ]\n}}\n', encoding="utf-8", newline="\n")
    print(f"wrote {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
