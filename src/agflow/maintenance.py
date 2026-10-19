"""Daily upkeep: look, remember, never change.

``agflow maintenance run`` (what the scheduled sidecar runs, and what a user can run by hand) does two read-only
things and writes one small cache file:

1. ``agflow sync check``  - is AG-Flow still compatible with the installed Antigravity? (:mod:`agflow.compat`)
2. ``agflow update check`` - is a newer AG-Flow published? (:mod:`agflow.update`; honours the update settings and the
   check interval, so a daily job asks GitHub at most once per interval)

It installs nothing, applies nothing and calls no model. The results are cached so the panel, ``agflow doctor`` and the
agent can show "Antigravity changed" or "AG-Flow 0.5.0 is available" without probing again: see :func:`health`.
"""

from __future__ import annotations

import contextlib
import time
from pathlib import Path
from typing import Any

from agflow import __version__, compat, fsutil, paths, update, versions

FILE = "maintenance-last.json"
STALE_AFTER_SECONDS = 3 * 24 * 3600.0   # a daily job that has not run for three days is worth mentioning


def state_file(directory: Path | None = None) -> Path:
    return (directory or paths.global_state_dir()) / FILE


def run(*, directory: Path | None = None, now: float | None = None, force_update_check: bool = False) -> dict[str, Any]:
    """Do the checks and cache the outcome. Never raises: a failing check is recorded, not thrown."""
    moment = time.time() if now is None else now
    result: dict[str, Any] = {"at": moment, "sync": "unknown", "update": "unknown", "errors": []}
    try:
        result["sync"] = compat.run(directory).status
    except Exception as exc:  # noqa: BLE001 - upkeep must not fail loudly in the background
        result["errors"].append(f"sync check: {type(exc).__name__}: {exc}")
    try:
        checked = update.check(automatic=True, force=force_update_check, directory=directory, now=moment)
        result["update"] = checked.status
        result["latest"] = checked.latest.version if checked.latest else None
        if checked.error:
            result["errors"].append(f"update check: {checked.error}")
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"update check: {type(exc).__name__}: {exc}")
    with contextlib.suppress(OSError):
        fsutil.write_json(state_file(directory), result)
    return result


def health(directory: Path | None = None, *, now: float | None = None) -> dict[str, Any]:
    """What is known right now without probing anything: the cached compatibility result, the cached update answer, and
    when upkeep last ran. Cheap and offline; safe for a page load or a doctor run."""
    moment = time.time() if now is None else now
    report = compat.load_last(directory)
    state = update.load_state(directory)
    latest = update.ReleaseInfo.from_json(state.get("latest"))
    cfg = update.load_config(directory)
    have, want = versions.parse(__version__), versions.parse(latest.version) if latest else None
    available = bool(have and want and want > have)
    try:
        last = fsutil.read_json(state_file(directory), default={})
    except (OSError, ValueError):
        last = {}
    ran_at = last.get("at") if isinstance(last, dict) and isinstance(last.get("at"), (int, float)) else None
    notes = []
    if report is not None and report.status in ("drift", "breaking"):
        notes.append(f"Antigravity {report.status}: run `agflow sync check`")
    if available:
        notes.append(f"AG-Flow {latest.version} is available: run `agflow update plan`")  # type: ignore[union-attr]
    return {
        "compat": {"status": report.status if report else None,
                   "agy_version": report.agy_version if report else None,
                   "checked_at": report.checked_at if report else None},
        "update": {"available": available, "latest": latest.version if latest else None,
                   "current": __version__, "channel": cfg.channel, "auto_check": cfg.auto_check,
                   "checked_at": state.get("checked_at"), "error": state.get("error")},
        "maintenance": {"last_run": ran_at, "stale": ran_at is None or moment - ran_at > STALE_AFTER_SECONDS},
        "notes": notes,
    }
