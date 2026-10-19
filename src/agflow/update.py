"""AG-Flow's own updates, part 1: find a newer release, decide whether it is safe for this machine, and say so.

Nothing in this module installs anything (that is :mod:`agflow.update_apply`, and only after the user says yes).

* **Source.** Only the GitHub Releases of one pinned repository (:mod:`agflow.release_feed`). Never PyPI: the project
  name is unclaimed there.
* **Checking is cheap and quiet.** One conditional request (ETag) at most once per interval, never on a hook or MCP
  hot path, and a network failure is a calm ``error`` result with the last known answer, never an exception.
* **A plan before any change.** :func:`plan` lists what an update would do and everything that blocks it: a
  development install, an Antigravity older than the release needs, an unsupported Python, no way to install, another
  update running or half-finished.
* **Release notes are untrusted text** (anyone with push access writes them): they are stripped of control characters
  and escape sequences before they reach a terminal or the dashboard.

Stdlib only.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any

from agflow import __version__, fsutil, paths, versions
from agflow import antigravity_probe as ap
from agflow.release_feed import DEFAULT_REPO, META_NAME, Feed, FeedError, Meta, Release, parse_meta, select

CONFIG_NAME = "update.json"
STATE_NAME = "update-state.json"
LOCK_NAME = "update.lock"
JOURNAL_NAME = "update-journal.json"
CHANNELS = ("stable", "pre")
NOTES_MAX = 4000
ERROR_RETRY_SECONDS = 3600  # after a failed check, try again after an hour whatever the interval is
LOCK_STALE_SECONDS = 1800

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")


def sanitize(text: Any, limit: int = NOTES_MAX) -> str:
    """Display-safe text: no escape sequences or control characters, bounded length, at most one blank line in a row."""
    clean = _CONTROL.sub("", _ANSI.sub("", str(text or "")).replace("\r\n", "\n").replace("\r", "\n"))
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"


def state_dir(override: Path | None = None) -> Path:
    return override or paths.global_state_dir()


# --- configuration --------------------------------------------------------------------------------


@dataclass
class Config:
    channel: str = "stable"       # "stable": final releases only; "pre": release candidates too
    auto_check: bool = True       # allow background checks (scheduled maintenance, dashboard); never auto-applies
    interval_hours: float = 24.0  # minimum time between checks
    repo: str = DEFAULT_REPO

    def to_json(self) -> dict[str, Any]:
        return {"channel": self.channel, "auto_check": self.auto_check, "interval_hours": self.interval_hours,
                "repo": self.repo}


def load_config(directory: Path | None = None) -> Config:
    """The saved configuration; any missing, wrong-typed or out-of-range field falls back to its default."""
    cfg = Config()
    try:
        data = fsutil.read_json(state_dir(directory) / CONFIG_NAME, default={})
    except (OSError, ValueError):
        return cfg
    if not isinstance(data, dict):
        return cfg
    if data.get("channel") in CHANNELS:
        cfg.channel = data["channel"]
    if isinstance(data.get("auto_check"), bool):
        cfg.auto_check = data["auto_check"]
    hours = data.get("interval_hours")
    if isinstance(hours, (int, float)) and not isinstance(hours, bool) and 1 <= hours <= 24 * 30:
        cfg.interval_hours = float(hours)
    repo = data.get("repo")
    if isinstance(repo, str) and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) and ".." not in repo:
        cfg.repo = repo
    return cfg


def save_config(cfg: Config, directory: Path | None = None) -> Path:
    path = state_dir(directory) / CONFIG_NAME
    fsutil.write_json(path, cfg.to_json())
    return path


# --- what was found -------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReleaseInfo:
    """The part of a release worth remembering between checks."""

    version: str
    tag: str
    published_at: str = ""
    prerelease: bool = False
    notes: str = ""
    wheel_name: str = ""
    wheel_size: int = 0

    @classmethod
    def from_release(cls, rel: Release) -> ReleaseInfo:
        wheel = rel.wheel
        return cls(str(rel.version), rel.tag, rel.published_at, rel.prerelease, sanitize(rel.notes),
                   wheel.name if wheel else "", wheel.size if wheel else 0)

    @classmethod
    def from_json(cls, data: Any) -> ReleaseInfo | None:
        if not isinstance(data, dict) or not isinstance(data.get("version"), str) or not data["version"]:
            return None
        size = data.get("wheel_size")
        return cls(data["version"], str(data.get("tag") or ""), str(data.get("published_at") or ""),
                   bool(data.get("prerelease")), sanitize(data.get("notes")), str(data.get("wheel_name") or ""),
                   size if isinstance(size, int) and not isinstance(size, bool) and size >= 0 else 0)

    def to_json(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class CheckResult:
    status: str  # up-to-date | available | ahead | none-published | disabled | error | unknown
    current: str
    latest: ReleaseInfo | None = None
    cached: bool = False
    checked_at: float = 0.0
    error: str = ""

    @property
    def available(self) -> bool:
        return self.status == "available"

    def to_json(self) -> dict[str, Any]:
        return {"status": self.status, "current": self.current,
                "latest": self.latest.to_json() if self.latest else None, "cached": self.cached,
                "checked_at": self.checked_at, "error": self.error}

    def lines(self) -> list[str]:
        latest = self.latest.version if self.latest else "?"
        text = {
            "available": f"AG-Flow {latest} is available (you have {self.current}). See `agflow update plan`.",
            "up-to-date": f"AG-Flow {self.current} is up to date.",
            "ahead": f"AG-Flow {self.current} is newer than the latest release ({latest}).",
            "none-published": "No installable AG-Flow release is published yet.",
            "disabled": "Automatic update checks are switched off (`agflow update config --auto-check`).",
            "error": f"Could not check for updates: {self.error}",
            "unknown": "Could not compare versions.",
        }[self.status]
        out = [text]
        if self.status == "error" and self.latest:
            out.append(f"  Last known release: {latest}.")
        return out


def load_state(directory: Path | None = None) -> dict[str, Any]:
    try:
        data = fsutil.read_json(state_dir(directory) / STATE_NAME, default={})
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state: dict[str, Any], directory: Path | None = None) -> None:
    with contextlib.suppress(OSError):  # a cache; never a reason to fail a check
        fsutil.write_json(state_dir(directory) / STATE_NAME, state)


def _status(current: str, latest: ReleaseInfo | None) -> str:
    if latest is None:
        return "none-published"
    have, want = versions.parse(current), versions.parse(latest.version)
    if have is None or want is None:
        return "unknown"
    return "available" if want > have else "up-to-date" if want == have else "ahead"


def due(state: dict[str, Any], cfg: Config, now: float) -> bool:
    """Is a network check due? Never faster than the interval; after a failure, retry after an hour."""
    checked = state.get("checked_at")
    if not isinstance(checked, (int, float)) or checked > now + 60:  # never checked, or the clock moved back
        return True
    wait = min(ERROR_RETRY_SECONDS, cfg.interval_hours * 3600) if state.get("error") else cfg.interval_hours * 3600
    return now - checked >= wait


def check(*, force: bool = False, automatic: bool = False, feed: Feed | None = None, directory: Path | None = None,
          config: Config | None = None, current: str = __version__, now: float | None = None) -> CheckResult:
    """Look for a newer release. ``automatic`` is a background check (it honours ``auto_check = false``); ``force``
    ignores the interval. Always returns a result; failures become ``status == "error"`` with the last known answer."""
    cfg = config or load_config(directory)
    state = load_state(directory)
    moment = time.time() if now is None else now
    latest = ReleaseInfo.from_json(state.get("latest"))
    if automatic and not cfg.auto_check and not force:
        return CheckResult("disabled", current, latest, True, float(state.get("checked_at") or 0))
    if not force and not due(state, cfg, moment) and state.get("channel", cfg.channel) == cfg.channel:
        problem = str(state.get("error") or "")  # a recent failure is remembered too: no hammering GitHub
        return CheckResult("error" if problem else _status(current, latest), current, latest, True,
                           float(state.get("checked_at") or 0), problem)
    try:
        got = (feed or Feed(cfg.repo)).list_releases(
            etag=state.get("etag") if state.get("channel") == cfg.channel else None)
    except FeedError as exc:
        state.update(checked_at=moment, error=str(exc), channel=cfg.channel)
        save_state(state, directory)
        return CheckResult("error", current, latest, False, moment, str(exc))
    if not got.not_modified:
        chosen = select(got.releases, include_prereleases=cfg.channel == "pre")
        latest = ReleaseInfo.from_release(chosen) if chosen else None
        state.update(etag=got.etag, latest=latest.to_json() if latest else None)
    state.update(checked_at=moment, channel=cfg.channel, error=None)
    save_state(state, directory)
    return CheckResult(_status(current, latest), current, latest, False, moment)


# --- how this copy of AG-Flow is installed ----------------------------------------


@dataclass(frozen=True)
class InstallMode:
    kind: str  # editable | source | uv-tool | venv | system
    package_dir: Path
    site_dir: Path
    python: str

    @property
    def updatable(self) -> bool:
        return self.kind not in ("editable", "source")


def detect_install_mode(package_dir: Path | None = None, python: str | None = None) -> InstallMode:
    pkg = (package_dir or Path(__file__).resolve().parent).resolve()
    site = pkg.parent
    py = python or sys.executable
    kind = "system"
    if package_dir is None:  # the installed metadata describes the running package, not an arbitrary folder
        try:
            direct = metadata.distribution("agflow").read_text("direct_url.json")
            if direct and json.loads(direct).get("dir_info", {}).get("editable"):
                kind = "editable"
        except (metadata.PackageNotFoundError, ValueError, OSError, AttributeError):
            pass
    if kind != "editable":
        if (site.parent / "pyproject.toml").is_file() and site.name == "src":
            kind = "source"
        elif {"uv", "tools"} <= {p.lower() for p in Path(sys.prefix).parts}:
            kind = "uv-tool"
        elif sys.prefix != getattr(sys, "base_prefix", sys.prefix):
            kind = "venv"
    return InstallMode(kind, pkg, site, py)


def installer_argv(wheel: Path, mode: InstallMode, *, sdk: bool, reinstall: bool = False) -> list[str] | None:
    """The command that installs ``wheel`` into this environment: ``uv pip`` when uv exists, else ``pip``; None when
    neither is available. The ``sdk`` extra is kept when the Antigravity SDK is installed."""
    requirement = f"agflow[sdk] @ {wheel.as_uri()}" if sdk else str(wheel)
    uv = shutil.which("uv")
    if uv:
        argv = [uv, "pip", "install", "--quiet", "--python", mode.python]
        return [*argv, *(["--reinstall-package", "agflow"] if reinstall else []), requirement]
    if importlib.util.find_spec("pip") is not None:
        argv = [mode.python, "-m", "pip", "install", "--quiet", "--disable-pip-version-check"]
        return [*argv, *(["--force-reinstall"] if reinstall else []), requirement]
    return None


# --- the plan -------------------------------------------------------------------------------------


@dataclass
class Plan:
    current: str
    target: str
    tag: str
    bump: str
    notes: str
    wheel_name: str
    wheel_size: int
    mode: InstallMode
    meta: Meta = field(default_factory=Meta)
    blocked: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    reinstall: bool = False
    release: Release | None = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        return not self.blocked

    def to_json(self) -> dict[str, Any]:
        return {"current": self.current, "target": self.target, "tag": self.tag, "bump": self.bump,
                "notes": self.notes, "wheel": self.wheel_name, "wheel_size": self.wheel_size,
                "install_mode": self.mode.kind, "ok": self.ok, "blocked": self.blocked, "warnings": self.warnings,
                "steps": self.steps, "tested_antigravity": list(self.meta.tested_antigravity),
                "min_antigravity": ".".join(map(str, self.meta.min_antigravity))}

    def lines(self) -> list[str]:
        out = [f"AG-Flow {self.current} -> {self.target} ({self.bump})"]
        out += [f"  blocked: {b}" for b in self.blocked]
        out += [f"  note: {w}" for w in self.warnings]
        if self.ok:
            out += ["  steps:", *[f"    {i}. {s}" for i, s in enumerate(self.steps, 1)]]
        return out


def lock_path(directory: Path | None = None) -> Path:
    return state_dir(directory) / LOCK_NAME


def journal_path(directory: Path | None = None) -> Path:
    return state_dir(directory) / JOURNAL_NAME


def lock_held(directory: Path | None = None, *, now: float | None = None) -> bool:
    """Is another update running right now? A lock older than half an hour is a crashed update's leftover."""
    try:
        age = (time.time() if now is None else now) - lock_path(directory).stat().st_mtime
    except OSError:
        return False
    return age < LOCK_STALE_SECONDS


def unfinished_journal(directory: Path | None = None) -> dict[str, Any] | None:
    try:
        data = fsutil.read_json(journal_path(directory), default=None)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("stage") not in (None, "done", "rolled-back") else None


STEPS = (
    "download the wheel and check it against SHA256SUMS (and GitHub's own digest)",
    "inspect the wheel (name, version, file layout) and try the new code on its own plugin before changing anything",
    "back up the installed package and AG-Flow's plugin files",
    "install the new version into this Python environment",
    "refresh the installed plugin with the new version, keeping the files you edited",
    "run doctor and the hook probe; any failure restores the backup automatically",
)


def plan(*, target: str | None = None, allow_breaking: bool = False, reinstall: bool = False, feed: Feed | None = None,
         directory: Path | None = None, config: Config | None = None, current: str = __version__,
         mode: InstallMode | None = None, snapshot: ap.Snapshot | None = None,
         python: tuple[int, ...] | None = None) -> Plan:
    """What updating would do and what stands in the way. Reads the release feed (and ``release.json``); changes
    nothing. Raises :class:`FeedError` only when the feed cannot be read at all."""
    cfg = config or load_config(directory)
    feed = feed or Feed(cfg.repo)
    mode = mode or detect_install_mode()
    got = feed.list_releases()
    if target:
        want = versions.parse(target)
        release = next((r for r in got.releases if r.usable and want is not None and r.version == want), None)
        if release is None:
            raise FeedError(f"{target} is not an installable release of {cfg.repo}")
    else:
        release = select(got.releases, include_prereleases=cfg.channel == "pre")
        if release is None:
            raise FeedError("no installable release is published yet")
    have = versions.parse(current)
    bump = versions.bump_kind(have, release.version) if have else "unknown"
    wheel = release.wheel
    result = Plan(current, str(release.version), release.tag, bump, sanitize(release.notes),
                  wheel.name if wheel else "", wheel.size if wheel else 0, mode, reinstall=reinstall, release=release)
    _check_release(result, release, feed, allow_breaking, reinstall, snapshot, python or sys.version_info[:3],
                   directory)
    return result


def _check_release(result: Plan, release: Release, feed: Feed, allow_breaking: bool, reinstall: bool,
                   snapshot: ap.Snapshot | None, python: tuple[int, ...], directory: Path | None) -> None:
    blocked, warnings = result.blocked, result.warnings
    if result.bump == "same" and not reinstall:
        blocked.append("this release is already installed (use --reinstall to repair it)")
    elif result.bump == "downgrade":
        blocked.append(f"{result.target} is older than the installed {result.current}: updates only move forward")
    if not result.mode.updatable:
        blocked.append("this AG-Flow runs from a source checkout (a development install): update it with git, not "
                       "with `agflow update`")
    elif not os.access(result.mode.site_dir, os.W_OK):
        blocked.append(f"{result.mode.site_dir} is not writable by this user")
    if result.mode.updatable and installer_argv(Path("x.whl"), result.mode, sdk=False) is None:
        blocked.append("neither `uv` nor `pip` is available in this environment to install the new version")
    if lock_held(directory):
        blocked.append("another update is running right now")
    if unfinished_journal(directory):
        blocked.append("a previous update did not finish: run `agflow update rollback` first")

    meta = Meta()
    asset = release.asset(META_NAME)
    if asset is not None:
        try:
            meta = parse_meta(feed.fetch_text(asset))
        except FeedError as exc:
            warnings.append(f"the release's compatibility file could not be read ({exc}); checks that need it were "
                            "skipped")
    else:
        warnings.append("this release does not declare which Antigravity it was tested against")
    result.meta = meta

    if meta.python:
        fits = versions.satisfies(meta.python, python)
        if fits is False:
            blocked.append(f"this release needs Python {meta.python}; this environment has "
                           f"{'.'.join(map(str, python))}")
    snap = snapshot if snapshot is not None else ap.probe()
    agy = versions.numeric_tuple(snap.agy_version or "")
    if meta.min_antigravity and agy and agy < meta.min_antigravity:
        blocked.append(f"this release needs Antigravity {'.'.join(map(str, meta.min_antigravity))} or newer; you have "
                       f"{snap.agy_version}. Update Antigravity first (`agy update`): AG-Flow never updates it")
    elif meta.tested_antigravity and snap.agy_version and snap.agy_version not in meta.tested_antigravity:
        warnings.append(f"not tested against your Antigravity {snap.agy_version} (tested: "
                        f"{', '.join(meta.tested_antigravity)}); `agflow sync check` re-checks it after the update")
    if meta.breaking or result.bump == "major":
        message = "this release declares breaking changes" if meta.breaking else "this is a major version"
        if allow_breaking:
            warnings.append(f"{message}; going ahead because --allow-breaking was given")
        else:
            blocked.append(f"{message}: read the release notes, then repeat with --allow-breaking")
    if release.prerelease:
        warnings.append("this is a pre-release")
    if meta.notes:
        warnings.append(sanitize(meta.notes, 500))
    result.steps = list(STEPS)
