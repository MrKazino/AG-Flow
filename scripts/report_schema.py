"""The key structure of a run's ``report.json``, for comparing a live SDK run with what agflow expects (F33).

    python scripts/report_schema.py <report.json> --snapshot tests/snapshots/run_report_keys.json [--update]

Only key names are compared, never values or types: a live run's summaries and timings differ every time, but a missing
or renamed key means the report no longer has the shape that the dashboard and the CLI read. Maps whose keys are user
data (``inputs``, ``rework_loops``) count as one opaque entry, and a list of objects contributes the union of its keys.
Standard library only: the nightly job runs it before anything else is installed.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path
from typing import Any

OPAQUE_MAPS = ("inputs", "rework_loops")


def key_schema(value: Any, key: str | None = None) -> Any:
    if key in OPAQUE_MAPS:
        return "<map>"
    if isinstance(value, dict):
        return {k: key_schema(v, k) for k, v in sorted(value.items())}
    if isinstance(value, list):
        merged: dict[str, Any] = {}
        for element in value:
            shape = key_schema(element)
            if isinstance(shape, dict):
                merged = _merge(merged, shape)
        return [merged] if merged else "<list>"
    return "<value>"


def _merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(out[k], v) if isinstance(out.get(k), dict) and isinstance(v, dict) else v
    return dict(sorted(out.items()))


def as_text(schema: Any) -> str:
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def compare(report: Any, expected: Any) -> str:
    """An empty string when the report has the expected keys, otherwise a readable unified diff."""
    actual, wanted = as_text(key_schema(report)), as_text(expected)
    if actual == wanted:
        return ""
    return "".join(difflib.unified_diff(wanted.splitlines(True), actual.splitlines(True), "expected (snapshot)",
                                        "actual (this run)"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("report", type=Path, help="a run's report.json")
    ap.add_argument("--snapshot", type=Path, required=True, help="the committed key structure")
    ap.add_argument("--update", action="store_true", help="write the snapshot instead of comparing")
    args = ap.parse_args(argv)
    report = json.loads(args.report.read_text(encoding="utf-8"))
    if args.update:
        args.snapshot.write_text(as_text(key_schema(report)), encoding="utf-8", newline="\n")  # LF on every platform
        print(f"wrote {args.snapshot}")
        return 0
    diff = compare(report, json.loads(args.snapshot.read_text(encoding="utf-8")))
    if diff:
        sys.stderr.write("report.json no longer has the keys agflow expects:\n" + diff)
        return 1
    print("report.json has the expected keys")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
