"""AG-Flow's own updates, part 2: apply a planned update, or undo it. Runs only when the user asked for it.

The order is built so that nothing is changed until the new version has proven itself, and every later failure is
undone automatically:

1. download the wheel; its SHA-256 must match ``SHA256SUMS`` **and** GitHub's own digest when GitHub reports one;
2. inspect the wheel without installing it (it is AG-Flow's, it is this version, nothing escapes its folders, no zip
   bomb) and run the new code on its own bundled plugin in a scratch folder (``agflow doctor --path``);
3. back up the installed package and every plugin file the installer will touch;
4. install the wheel into this Python environment (``uv pip`` or ``pip``);
5. refresh each existing AG-Flow install with the *new* code (a fresh process), then put the user's edited files back
   on top (:mod:`agflow.plugin_files`);
6. run the new version's doctor and hook probe (``agflow update verify``).

A failure at step 3 to 6 restores the backup and re-verifies the old version. A crash leaves a journal and a lock;
``agflow update rollback`` restores from the journal. Antigravity itself is never touched.

Everything the process needs after the package is replaced is imported *before* it is replaced
(:func:`_preload`): the running process still holds the old code and must not mix in the new files.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import os
import re
import shutil
import stat
import subprocess
import time
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agflow import antigravity_probe as ap
from agflow import fixes, fsutil, paths, plugin_files, versions
from agflow.release_feed import SUMS_NAME, Feed, FeedError, parse_sums
from agflow.update import (
    LOCK_STALE_SECONDS,
    InstallMode,
    Plan,
    detect_install_mode,
    installer_argv,
    journal_path,
    load_config,
    load_state,
    lock_path,
    save_state,
    state_dir,
)

KEEP_BACKUPS = 5
MAX_WHEEL_FILES = 5000
MAX_WHEEL_UNPACKED = 200 * 1024 * 1024
STEP_TIMEOUT = 900.0
_DIST_INFO = re.compile(r"^agflow-[^/]+\.dist-info$")
_VERSION_LINE = re.compile(r"""^__version__\s*=\s*["']([^"']+)["']""", re.M)

_PRELOAD = ("agflow.compat", "agflow.fixes", "agflow.install", "agflow.plugin_files", "agflow.release_feed",
            "agflow.update", "agflow.versions", "agflow.antigravity_probe", "agflow.fsutil", "agflow.paths",
            "agflow.inventory", "agflow.validate", "agflow.models", "agflow.hookfile", "agflow.frontmatter")


class UpdateError(RuntimeError):
    """An update step failed. The message is for the user."""


@dataclass
class Proc:
    returncode: int
    stdout: str = ""
    stderr: str = ""


Runner = Callable[..., Proc]


def run_command(argv: list[str], env: dict[str, str] | None = None, cwd: Path | None = None,
                timeout: float = STEP_TIMEOUT) -> Proc:
    """Run a command to completion; a missing program or a timeout is a failed Proc, never an exception."""
    try:
        done = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                              check=False, stdin=subprocess.DEVNULL, env=env, cwd=cwd)
    except subprocess.TimeoutExpired:
        return Proc(124, "", f"timed out after {int(timeout)} s")
    except (OSError, ValueError) as exc:
        return Proc(127, "", f"{type(exc).__name__}: {exc}")
    return Proc(done.returncode, done.stdout or "", done.stderr or "")


def _preload() -> None:
    for name in _PRELOAD:
        importlib.import_module(name)


def _tail(proc: Proc) -> str:
    """The end of a failed command's output, joined (a long error message arrives wrapped over several lines)."""
    lines = [ln.strip() for ln in (proc.stderr or proc.stdout).strip().splitlines() if ln.strip()]
    return " ".join(lines[-4:])[-500:] if lines else f"exit code {proc.returncode}"


def _json_from(text: str) -> dict[str, Any] | None:
    """The last JSON object printed in ``text`` (a command may print notes before it)."""
    for line in reversed(text.strip().splitlines()):
        if line.lstrip().startswith("{"):
            try:
                data = json.loads(line)
            except ValueError:
                continue
            return data if isinstance(data, dict) else None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


@dataclass
class Outcome:
    ok: bool
    from_version: str
    to_version: str
    log: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    rolled_back: bool = False
    merge: dict[str, list[str]] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return self.__dict__.copy()


# --- lock and journal -----------------------------------------------------------------------------


@contextlib.contextmanager
def _lock(directory: Path | None) -> Iterator[None]:
    path = lock_path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if attempt or time.time() - path.stat().st_mtime < LOCK_STALE_SECONDS:
                raise UpdateError("another update is running (or died less than 30 minutes ago): "
                                  f"if you are sure it is not, delete {path}") from None
            path.unlink(missing_ok=True)  # a crashed update's leftover
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"{os.getpid()} {time.time()}\n")
        yield
    finally:
        path.unlink(missing_ok=True)


class Journal:
    """The record of one update, rewritten after every stage, so a crash can be undone from it."""

    def __init__(self, directory: Path | None, data: dict[str, Any]):
        self.path, self.data = journal_path(directory), data
        self.save()

    def update(self, **fields: Any) -> None:
        self.data.update(fields)
        self.save()

    def save(self) -> None:
        fsutil.write_json(self.path, self.data)


def work_root(directory: Path | None = None) -> Path:
    """Scratch space for downloads and unpacked wheels. Deliberately not ``install-tmp``: the installer empties that
    folder while it swaps a plugin, which would delete the working folder of the very command doing the refresh."""
    return state_dir(directory) / "update-tmp"


def load_journal(directory: Path | None = None) -> dict[str, Any] | None:
    try:
        data = fsutil.read_json(journal_path(directory), default=None)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# --- the wheel ------------------------------------------------------------------------------------


def _verify_download(plan: Plan, feed: Feed, workdir: Path, say: Callable[[str], None]) -> Path:
    release = plan.release
    wheel_asset = release.wheel if release else None
    sums_asset = release.asset(SUMS_NAME) if release else None
    if release is None or wheel_asset is None or sums_asset is None:
        raise UpdateError("the release has no wheel or no SHA256SUMS")
    path, digest, size = feed.download(wheel_asset, workdir / wheel_asset.name)
    say(f"downloaded {wheel_asset.name} ({size // 1024} KB)")
    expected = parse_sums(feed.fetch_text(sums_asset)).get(wheel_asset.name)
    if expected is None:
        raise UpdateError(f"SHA256SUMS does not list {wheel_asset.name}")
    if digest != expected:
        raise UpdateError(f"the download does not match SHA256SUMS ({wheel_asset.name}): refused")
    if wheel_asset.digest and wheel_asset.digest.lower() != f"sha256:{digest}":
        raise UpdateError("the download does not match the digest GitHub reports for it: refused")
    say("checksum verified" + (" (SHA256SUMS and GitHub's digest agree)" if wheel_asset.digest else " (SHA256SUMS)"))
    return path


def inspect_wheel(path: Path, expected_version: str) -> None:
    """Refuse anything that is not exactly AG-Flow's wheel for ``expected_version``. Reads, never extracts."""
    want = versions.parse(expected_version)
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise UpdateError(f"{path.name} is not a readable wheel ({type(exc).__name__})") from None
    with zf:
        infos = zf.infolist()
        if not infos or len(infos) > MAX_WHEEL_FILES or sum(i.file_size for i in infos) > MAX_WHEEL_UNPACKED:
            raise UpdateError("the wheel is empty or unreasonably large when unpacked: refused")
        dist_info = ""
        for info in infos:
            name = info.filename
            parts = name.split("/")
            if (name.startswith("/") or "\\" in name or ".." in parts or re.match(r"^[A-Za-z]:", name)
                    or stat.S_ISLNK(info.external_attr >> 16)):
                raise UpdateError(f"the wheel contains an unsafe entry ({name!r}): refused")
            top = parts[0]
            if top == "agflow":
                continue
            if _DIST_INFO.match(top):
                dist_info = top
                continue
            raise UpdateError(f"the wheel contains {top!r}, which is not AG-Flow's: refused")
        if not dist_info:
            raise UpdateError("the wheel has no metadata")
        meta = zf.read(f"{dist_info}/METADATA").decode("utf-8", errors="replace")
        name_line = re.search(r"^Name:\s*(\S+)", meta, re.M)
        version_line = re.search(r"^Version:\s*(\S+)", meta, re.M)
        if not name_line or name_line.group(1).lower().replace("_", "-") != "agflow":
            raise UpdateError("the wheel is not AG-Flow's")
        if want is None or not version_line or versions.parse(version_line.group(1)) != want:
            raise UpdateError(f"the wheel's version ({version_line.group(1) if version_line else '?'}) is not "
                              f"{expected_version}: refused")
        try:
            init = zf.read("agflow/__init__.py").decode("utf-8", errors="replace")
        except KeyError:
            raise UpdateError("the wheel has no agflow package") from None
        inner = _VERSION_LINE.search(init)
        if not inner or versions.parse(inner.group(1)) != want:
            raise UpdateError("the package inside the wheel reports a different version than its metadata: refused")


def _extract(path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            target = (dest / info.filename).resolve()
            if root not in target.parents and target != root:
                raise UpdateError(f"the wheel contains an unsafe entry ({info.filename!r}): refused")
        zf.extractall(dest)


def _preflight(stage: Path, mode: InstallMode, run: Runner, say: Callable[[str], None]) -> None:
    """Run the new code on its own bundled plugin, in a scratch copy, before anything on this machine is changed."""
    plugin = stage / "agflow" / "_plugin"
    if not (plugin / "plugin.json").is_file():
        raise UpdateError("the new version carries no plugin to install")
    env = dict(os.environ, PYTHONPATH=str(stage), PYTHONDONTWRITEBYTECODE="1")
    proc = run([mode.python, "-s", "-m", "agflow", "doctor", "--path", str(plugin), "--no-global", "--json"],
               env, stage.parent, 120.0)
    data = _json_from(proc.stdout)
    if data is None:
        if "ModuleNotFoundError" in proc.stderr or "ImportError" in proc.stderr:
            say("the new version needs packages this environment lacks; the installer will fetch them")
            return
        raise UpdateError(f"the new version could not check its own plugin: {_tail(proc)}")
    errors = (data.get("counts") or {}).get("error", 0)
    if errors:
        raise UpdateError(f"the new version's own plugin has {errors} error(s) in doctor: refused")
    say("the new version passes doctor on its own plugin")


# --- backup and restore of the installed package --------------------------------------------------


def _dist_infos(mode: InstallMode) -> list[Path]:
    return sorted(p for p in mode.site_dir.glob("agflow-*.dist-info") if p.is_dir())


def _backup_package(mode: InstallMode, dest: Path) -> list[str]:
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(mode.package_dir, dest / "agflow", symlinks=True)
    names = []
    for info in _dist_infos(mode):
        shutil.copytree(info, dest / info.name, symlinks=True)
        names.append(info.name)
    return names


def _restore_package(mode: InstallMode, backup: Path, dist_infos: list[str]) -> None:
    saved = backup / "agflow"
    if not saved.is_dir():
        raise UpdateError(f"the package backup {saved} is missing")
    shutil.rmtree(mode.package_dir, ignore_errors=True)
    shutil.copytree(saved, mode.package_dir, symlinks=True)
    for current in _dist_infos(mode):
        shutil.rmtree(current, ignore_errors=True)  # the new version's metadata folder has a different name
    for name in dist_infos:
        shutil.copytree(backup / name, mode.site_dir / name, symlinks=True)


# --- the installs to refresh ----------------------------------------------------------------------


@dataclass
class ScopeInstall:
    scope: str
    workspace: Path | None
    target: Path
    manifest: Path
    old_recorded: dict[str, str]
    snapshot: Path | None = None


def _scopes(workspace: Path | None, old_bundle: dict[str, str]) -> list[ScopeInstall]:
    from agflow import install as inst

    out = []
    for scope, target in inst.installed_copies(workspace).items():
        if target.is_symlink():
            continue  # a linked (development) install follows its source on its own
        ws = workspace if scope == "workspace" else None
        manifest = inst.manifest_path(scope, ws)  # type: ignore[arg-type]
        recorded = plugin_files.load(plugin_files.record_path(manifest)) or old_bundle
        out.append(ScopeInstall(scope, ws, target, manifest, recorded))
    return out


def _refresh_argv(item: ScopeInstall, mode: InstallMode, both: bool) -> list[str]:
    """The new version's ``agflow update refresh``: the same installer, with the install's own settings kept."""
    manifest = fsutil.read_json(item.manifest, {}) or {}
    argv = [mode.python, "-m", "agflow", "update", "refresh", "--scope", item.scope]
    if item.scope == "workspace" and item.workspace is not None:
        argv += ["--workspace-root", str(item.workspace)]
    if manifest.get("profile"):
        argv += ["--profile", str(manifest["profile"])]
    hooks_file = paths.plugin_layout(item.target).hooks_file
    if hooks_file is None or not hooks_file.is_file():
        argv += ["--no-hooks"]
    if not manifest.get("managed_blocks"):
        argv += ["--no-gemini-md"]
    if both:
        argv += ["--allow-duplicate"]
    return argv


# --- apply ----------------------------------------------------------------------------------------


def apply(plan: Plan, *, feed: Feed | None = None, directory: Path | None = None, workspace: Path | None = None,
          run: Runner = run_command, log: Callable[[str], None] | None = None,
          bundle: Callable[[], Path] = paths.bundled_plugin_dir) -> Outcome:
    """Apply ``plan``. Returns an :class:`Outcome`; a failed update is an ``ok=False`` outcome that has already been
    rolled back, not an exception. Raises :class:`UpdateError` only for a plan that is blocked or a held lock."""
    if not plan.ok or plan.release is None:
        raise UpdateError("; ".join(plan.blocked) or "there is nothing to apply")
    _preload()
    cfg = load_config(directory)
    feed = feed or Feed(cfg.repo)
    outcome = Outcome(False, plan.current, plan.target)

    def say(message: str) -> None:
        outcome.log.append(message)
        if log:
            log(message)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    workdir = work_root(directory) / f"update-{stamp}"
    backup_dir = paths.backups_dir() / f"{stamp}-update"
    with _lock(directory):
        journal = Journal(directory, {"id": stamp, "from": plan.current, "to": plan.target, "stage": "started",
                                      "backup_dir": str(backup_dir), "dist_infos": [], "txn_ops": [],
                                      "started": time.time()})
        txn = fixes.Txn()
        try:
            wheel = _verify_download(plan, feed, workdir, say)
            inspect_wheel(wheel, plan.target)
            stage = workdir / "unpacked"
            _extract(wheel, stage)
            say("inspected the wheel")
            _preflight(stage, plan.mode, run, say)

            old_bundle = plugin_files.pristine(bundle())
            scopes = _scopes(workspace, old_bundle)
            journal.update(stage="backing-up")
            dist_infos = _backup_package(plan.mode, backup_dir / "pkg")
            for item in scopes:
                _touch_scope(txn, item)
                item.snapshot = backup_dir / f"plugin-{item.scope}"
                shutil.copytree(item.target, item.snapshot, symlinks=True)
            journal.update(stage="backed-up", dist_infos=dist_infos, txn_ops=txn.ops)
            say("backed up the installed package and plugin files")

            sdk = ap.sdk_version() is not None
            argv = installer_argv(wheel, plan.mode, sdk=sdk, reinstall=plan.reinstall)
            if argv is None:
                raise UpdateError("no installer (uv or pip) is available")
            proc = run(argv, None, workdir, STEP_TIMEOUT)
            if proc.returncode != 0:
                raise UpdateError(f"installing the new version failed: {_tail(proc)}")
            journal.update(stage="package-installed")
            say(f"installed AG-Flow {plan.target}")

            new_bundle = plugin_files.pristine(bundle())
            for item in scopes:
                proc = run(_refresh_argv(item, plan.mode, both=len(scopes) > 1), None, workdir, STEP_TIMEOUT)
                if proc.returncode != 0:
                    raise UpdateError(f"refreshing the {item.scope} plugin failed: {_tail(proc)}")
                # what the new install itself wrote (its record) is the exact upstream state; the bundle is the
                # fallback and holds a few files the installer does not copy into the plugin folder
                upstream = plugin_files.load(plugin_files.record_path(item.manifest)) or new_bundle
                report = plugin_files.reapply_user_edits(
                    item.snapshot, item.target, item.old_recorded, upstream,  # type: ignore[arg-type]
                    state_dir(directory) / "incoming" / f"{plan.target}-{item.scope}")
                outcome.merge[item.scope] = report.summary()
                kept = "" if report.clean else f" ({len(report.summary())} of your edits kept)"
                say(f"refreshed the {item.scope} plugin{kept}")
            journal.update(stage="plugin-refreshed")

            _verify_installed(plan.target, plan.mode, run, say)
            journal.update(stage="done")
            outcome.ok = True
            _remember(directory, outcome)
            _prune_backups(backup_dir.parent)
        except Exception as exc:  # noqa: BLE001 - whatever went wrong, the machine must end up as it was
            known = isinstance(exc, (UpdateError, FeedError, OSError, zipfile.BadZipFile, shutil.Error))
            outcome.problems.append(str(exc) if known else f"unexpected error: {type(exc).__name__}: {exc}")
            say(f"failed: {outcome.problems[-1]}")
            if journal.data.get("stage") != "started":  # otherwise nothing had been changed yet
                _undo(journal.data, plan.mode, txn.ops, run, outcome, say)
            journal.update(stage="rolled-back")
            _remember(directory, outcome)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
            with contextlib.suppress(OSError):
                work_root(directory).rmdir()  # only when empty
    return outcome


def _touch_scope(txn: fixes.Txn, item: ScopeInstall) -> None:
    """Back up everything the installer may change for this install (the same set ``agflow fix`` backs up)."""
    manifest_data = fsutil.read_json(item.manifest, {}) or {}
    for path in [*manifest_data.get("created", []), *manifest_data.get("modified", {}),
                 *manifest_data.get("managed_blocks", [])]:
        txn.touch(Path(path))
    txn.touch(item.target)
    txn.touch(item.manifest)
    txn.touch(plugin_files.record_path(item.manifest))
    gemini_md = paths.global_layout().gemini_md
    if item.scope == "global" and gemini_md is not None:
        txn.touch(gemini_md)


def _verify_installed(version: str, mode: InstallMode, run: Runner, say: Callable[[str], None]) -> None:
    proc = run([mode.python, "-m", "agflow", "update", "verify", "--json"], None, None, 300.0)
    data = _json_from(proc.stdout)
    if proc.returncode != 0 and data is None:
        raise UpdateError(f"the new version did not start: {_tail(proc)}")
    if data is None:
        raise UpdateError("the new version's verification printed nothing readable")
    if str(data.get("version")) != version:
        raise UpdateError(f"after the install this environment runs AG-Flow {data.get('version')}, not {version}")
    problems = data.get("problems") or []
    if problems:
        raise UpdateError(f"the new version fails its own checks: {problems[0]}")
    say("the new version passes doctor and the hook probe")


def _undo(journal: dict[str, Any], mode: InstallMode, ops: list[dict[str, Any]], run: Runner, outcome: Outcome,
          say: Callable[[str], None]) -> None:
    """Restore the package and the plugin files from the backup, then check the old version works again."""
    try:
        backup = Path(journal["backup_dir"]) / "pkg"
        if backup.is_dir():
            _restore_package(mode, backup, list(journal.get("dist_infos") or []))
        fixes.restore_ops(ops or list(journal.get("txn_ops") or []))
    except (OSError, UpdateError, shutil.Error, KeyError) as exc:
        outcome.problems.append(f"the automatic rollback failed ({exc}); the backup is in {journal.get('backup_dir')}")
        say(outcome.problems[-1])
        return
    outcome.rolled_back = True
    say(f"restored AG-Flow {journal.get('from')} and its plugin files from the backup")
    try:
        _verify_installed(str(journal.get("from")), mode, run, say)
    except UpdateError as exc:
        outcome.problems.append(f"the restored version did not pass its check ({exc}); run `agflow doctor`")
        say(outcome.problems[-1])


def rollback(*, directory: Path | None = None, run: Runner = run_command, log: Callable[[str], None] | None = None,
             mode: InstallMode | None = None) -> Outcome:
    """Undo the most recent update (finished or interrupted) from its journal and backup."""
    journal = load_journal(directory)
    if journal is None or not journal.get("backup_dir"):
        raise UpdateError("there is no update to roll back")
    if journal.get("stage") == "rolled-back":
        raise UpdateError("the last update has already been rolled back")
    if not (Path(journal["backup_dir"]) / "pkg").is_dir():
        raise UpdateError(f"the backup of the last update is gone ({journal['backup_dir']})")
    _preload()
    outcome = Outcome(False, str(journal.get("to")), str(journal.get("from")))

    def say(message: str) -> None:
        outcome.log.append(message)
        if log:
            log(message)

    with _lock(directory):
        _undo(journal, mode or detect_install_mode(), [], run, outcome, say)
        outcome.ok = outcome.rolled_back and not outcome.problems
        if outcome.rolled_back:
            Journal(directory, dict(journal, stage="rolled-back"))
    _remember(directory, outcome)
    return outcome


def _remember(directory: Path | None, outcome: Outcome) -> None:
    state = load_state(directory)
    state["last_apply"] = {"from": outcome.from_version, "to": outcome.to_version, "ok": outcome.ok,
                           "rolled_back": outcome.rolled_back, "at": time.time(), "problems": outcome.problems[:3]}
    save_state(state, directory)


def _prune_backups(root: Path) -> None:
    """Keep the newest few update backups; they hold whole copies of the package."""
    with contextlib.suppress(OSError):
        old = sorted(p for p in root.glob("*-update") if p.is_dir())[:-KEEP_BACKUPS]
        for path in old:
            shutil.rmtree(path, ignore_errors=True)
