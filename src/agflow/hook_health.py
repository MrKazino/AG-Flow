"""A durable, cheap record of hook calls agflow could not make sense of.

The safety hooks must never break the agent, so when Antigravity sends something agflow does not understand they
return ``{}``. That used to be the whole story, which meant a change in Antigravity's payload format switched the
25-rule guard off with no sign anywhere. Every such call is now appended here, and ``agflow doctor`` and the
dashboard read it back.

Append-only JSON lines (``hook-health.jsonl`` under agflow's global state dir): concurrent hook processes cannot lose
each other's records, and nothing is read-modify-written. Records hold *key names* and a kind, never argument values.
The file is rotated when it grows, so a permanently blind guard cannot fill the disk.

Stdlib only: imported on the hook fast path.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

from agflow import paths

FILE = "hook-health.jsonl"
MAX_BYTES = 256 * 1024
WINDOW_DAYS = 7  # alarms older than this no longer count: the warning clears itself once the cause is fixed


def health_file() -> Path:
    return paths.global_state_dir() / FILE


def record(kind: str, event: str, keys: list[str], *, detail: str = "", now: float | None = None) -> None:
    """Append one record. ``kind`` is e.g. ``unknown_shape``, ``guard_blind:command``, ``payload_not_json``,
    ``empty_payload`` or ``handler_error``; ``keys`` are payload key names (never values)."""
    f = health_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    entry: dict[str, Any] = {"ts": time.time() if now is None else now, "kind": kind, "event": event,
                             "keys": sorted(set(keys))[:40]}
    if detail:
        entry["detail"] = detail[:200]
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    if f.stat().st_size > MAX_BYTES:
        os.replace(f, f.with_name(FILE + ".1"))  # keeps one previous generation


def _read(f: Path) -> list[dict[str, Any]]:
    try:
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # a torn line from a crash mid-write: skip it, the rest is still good
        if isinstance(entry, dict) and isinstance(entry.get("ts"), (int, float)) and entry.get("kind"):
            out.append(entry)
    return out


def summary(*, now: float | None = None, window_days: int = WINDOW_DAYS) -> dict[str, Any] | None:
    """What happened in the last ``window_days``, or ``None`` when the guard has been healthy."""
    f = health_file()
    cutoff = (time.time() if now is None else now) - window_days * 86400
    recent = [e for e in _read(f.with_name(FILE + ".1")) + _read(f) if e["ts"] >= cutoff]
    if not recent:
        return None
    last = max(recent, key=lambda e: e["ts"])
    return {"total": len(recent), "counts": dict(Counter(e["kind"] for e in recent)),
            "first_seen": min(e["ts"] for e in recent), "last_seen": last["ts"], "last_event": last["event"],
            "last_keys": last.get("keys", []), "window_days": window_days}
