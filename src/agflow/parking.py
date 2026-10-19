"""Markers that say "agflow disabled this", written next to every parked item.

A disabled skill, agent or rule is moved out of Antigravity's discovery paths into ``<state>/disabled/<kind>s/<scope>/``
and recorded in ``disabled/index.json``. That works only while two things stay true: nothing moves the item back, and
Antigravity never scans the parking area. The marker is a second, independent record that travels *with* the item: a
``.agflow-disabled`` file inside a parked folder, or ``<file>.agflow-disabled`` beside a parked file. It lets agflow

* recognise a disabled item that ended up in a scanned location again (restored from a backup, moved by hand), and warn
  that Antigravity is loading it, and
* rebuild the index entry of a parked item whose entry was lost.

The marker holds the item's kind, name, scope, original location and the time it was parked.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path
from typing import Any

from agflow.fsutil import atomic_write_text

MARKER = ".agflow-disabled"


def marker_path(movable: Path) -> Path:
    """Inside a folder (skill, agent folder); beside a single file (agent or rule)."""
    return movable / MARKER if movable.is_dir() else movable.with_name(movable.name + MARKER)


def write_marker(movable: Path, entry: dict[str, Any]) -> None:
    body = {key: entry.get(key) for key in ("kind", "name", "scope", "original_path")}
    body["time"] = time.time()
    atomic_write_text(marker_path(movable), json.dumps(body, indent=2) + "\n")


def has_marker(movable: Path) -> bool:
    return marker_path(movable).is_file()


def read_marker(movable: Path) -> dict[str, Any]:
    """The marker's content; ``{}`` for a marker that cannot be read (it still means "disabled")."""
    try:
        data = json.loads(marker_path(movable).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def remove_marker(movable: Path) -> None:
    with contextlib.suppress(OSError):  # one that stays is reported as a leak by doctor; it must not stop an enable
        marker_path(movable).unlink(missing_ok=True)


def parked_items(parking_root: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Every marked item under ``<state>/disabled``: (the parked folder or file, its marker)."""
    found: list[tuple[Path, dict[str, Any]]] = []
    if not parking_root.is_dir():
        return found
    for kind_dir in sorted(p for p in parking_root.iterdir() if p.is_dir()):
        for scope_dir in sorted(p for p in kind_dir.iterdir() if p.is_dir()):
            for child in sorted(scope_dir.iterdir()):
                if child.name.endswith(MARKER):
                    continue  # a sidecar; it is read through the file it belongs to
                if has_marker(child):
                    found.append((child, read_marker(child)))
    return found


def same_path(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
