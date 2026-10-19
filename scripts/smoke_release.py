#!/usr/bin/env python3
"""Release smoke test: the update path with real wheels, a real virtual environment and the real installer.

    python scripts/smoke_release.py [--keep]

1. builds two wheels from copies of this source tree (version OLD and version NEW);
2. creates a throwaway venv, installs OLD into it and runs ``agflow install`` under a **sandboxed HOME**;
3. serves NEW (with SHA256SUMS and release.json) from a fake GitHub on 127.0.0.1;
4. runs a real ``plan`` + ``apply`` inside that venv, checks the new version and the user's edited plugin file,
   rolls back and checks the old version again;
5. offers a release whose code cannot start and checks it is refused with the venv untouched.

Needs ``uv`` and the dependencies (from uv's cache or the network). Never touches the real ``~/.gemini`` or the agflow
you have installed. Exit code 0 when every check holds.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from agflow import paths  # noqa: E402
from tests.fake_github import REPO, FakeGitHub  # noqa: E402

OLD, NEW = "0.3.9", "0.4.0"
NEVER_COPIED = {"node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", "dist", "build", ".coverage", "htmlcov"}
KEPT_HIDDEN = {".github", ".gitignore", ".gitattributes"}


def IGNORE(directory: str, names: list[str]) -> set[str]:  # noqa: N802 - used like shutil.ignore_patterns(...)
    """What a build never needs: caches and environments everywhere and, at the top level, every hidden folder (the
    environment, other worktrees and tool state live there) except the repository's own."""
    skipped = {name for name in names if name in NEVER_COPIED or name.endswith(".egg-info")}
    if Path(directory).resolve() == ROOT:
        skipped |= {name for name in names if name.startswith(".") and name not in KEPT_HIDDEN}
    return skipped


checks: list[tuple[str, bool, str]] = []


def sandbox_locations(home: Path) -> tuple[Path, Path]:
    """Where an install under ``home`` puts the tdd skill and its install record, asked of agflow.paths itself."""
    saved = {key: os.environ.get(key) for key in ("HOME", "USERPROFILE")}
    os.environ.update(HOME=str(home), USERPROFILE=str(home))
    try:
        plugin = paths.plugin_layout(paths.global_layout().plugins_dir / paths.PLUGIN_NAME)  # type: ignore[operator]
        return plugin.skills_dir / "tdd" / "SKILL.md", paths.global_state_dir() / "install.files.json"  # type: ignore

    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok, detail))
    print(("  ok    " if ok else "  FAIL  ") + name + (f"  ({detail})" if detail and not ok else ""), flush=True)


def run(argv: list[str], env: dict[str, str] | None = None,
        cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, cwd=cwd,
                          check=False, stdin=subprocess.DEVNULL, timeout=900)


def copy_source(dest: Path, version: str, *, break_import: bool = False) -> Path:
    shutil.copytree(ROOT, dest, ignore=IGNORE)
    init = dest / "src" / "agflow" / "__init__.py"
    init.write_text(re.sub(r'__version__ = "[^"]+"', f'__version__ = "{version}"', init.read_text(encoding="utf-8")),
                    encoding="utf-8")
    if break_import:
        with init.open("a", encoding="utf-8") as fh:
            fh.write('\nraise RuntimeError("this release cannot start")\n')
    pyproject = dest / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    pyproject.write_text(re.sub(r'(?m)^version = "[^"]+"', f'version = "{version}"', text, count=1), encoding="utf-8")
    return dest


def build(src: Path, out: Path) -> Path:
    done = run(["uv", "build", "--wheel", "--out-dir", str(out), str(src)])
    wheels = list(out.glob("agflow-*.whl"))
    if done.returncode != 0 or len(wheels) != 1:
        raise SystemExit(f"building the wheel failed:\n{done.stdout}\n{done.stderr}")
    return wheels[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keep", action="store_true", help="keep the scratch folder and print its path")
    args = parser.parse_args()
    if shutil.which("uv") is None:
        print("uv is required for the smoke test", file=sys.stderr)
        return 2

    work = Path(tempfile.mkdtemp(prefix="agflow smoke "))   # a space in the path on purpose
    home = work / "home"
    home.mkdir()
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), PYTHONUTF8="1")
    env.pop("AGFLOW_GITHUB_TOKEN", None)
    gh = FakeGitHub().start()
    try:
        print(f"scratch folder: {work}")
        print("building wheels ...", flush=True)
        old_whl = build(copy_source(work / "src-old", OLD), work / "dist-old")
        new_whl = build(copy_source(work / "src-new", NEW), work / "dist-new")
        bad_whl = build(copy_source(work / "src-bad", "0.4.1", break_import=True), work / "dist-bad")
        names = {n for n in __import__("zipfile").ZipFile(new_whl).namelist()}
        check("the wheel carries the bundled plugin", "agflow/_plugin/plugin.json" in names)
        check("the wheel carries the compatibility baseline", "agflow/data/compat_baseline.json" in names)

        venv = work / "venv"
        check("venv created", run(["uv", "venv", "--quiet", str(venv)]).returncode == 0)
        py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        done = run(["uv", "pip", "install", "--quiet", "--python", str(py), str(old_whl)])
        check("old version installed into the venv", done.returncode == 0, done.stderr[-300:])

        def agflow(*argv: str) -> subprocess.CompletedProcess[str]:
            return run([str(py), "-m", "agflow", *argv], env=env, cwd=work)

        check("old version runs", agflow("version").stdout.strip() == f"agflow {OLD}")
        installed = agflow("install")
        check("agflow install (sandboxed HOME)", installed.returncode == 0, installed.stderr[-300:])
        skill, record = sandbox_locations(home)
        skill.write_text(skill.read_text(encoding="utf-8") + "\nMY LOCAL EDIT\n", encoding="utf-8")
        check("the install recorded what it installed", record.is_file())

        def publish(wheel: Path, version: str) -> None:
            data = wheel.read_bytes()
            meta = {"version": version, "min_antigravity": "0.0.1", "python": ">=3.10"}
            gh.add_release(version, wheel=data, meta=meta, notes="smoke release")

        publish(bad_whl, "0.4.1")
        publish(new_whl, NEW)
        driver = work / "drive.py"
        driver.write_text(DRIVER, encoding="utf-8")

        def drive(action: str, *extra: str) -> dict:
            done = run([str(py), str(driver), action, gh.base, REPO, *extra], env=env, cwd=work)
            line = next((ln for ln in reversed(done.stdout.splitlines()) if ln.startswith("{")), "{}")
            try:
                return json.loads(line)
            except ValueError:
                return {"error": (done.stdout + done.stderr)[-500:]}

        print("update to the broken release (must be refused, nothing changed) ...", flush=True)
        refused = drive("apply", "0.4.1")
        check("a release that cannot start is refused", refused.get("ok") is False and not refused.get("rolled_back"),
              json.dumps(refused)[:300])
        check("... and the venv still runs the old version", agflow("version").stdout.strip() == f"agflow {OLD}")

        print("update to the good release ...", flush=True)
        outcome = drive("apply", NEW)
        check("the update succeeded", outcome.get("ok") is True, json.dumps(outcome)[:400])
        check("the venv now runs the new version", agflow("version").stdout.strip() == f"agflow {NEW}")
        check("the user's edit to a plugin file survived", "MY LOCAL EDIT" in skill.read_text(encoding="utf-8"))
        verify = agflow("update", "verify", "--json")
        check("the new version passes its own verification", verify.returncode == 0, verify.stdout[-300:])

        print("rolling back ...", flush=True)
        back = drive("rollback")
        check("rollback succeeded", back.get("ok") is True, json.dumps(back)[:400])
        check("the venv runs the old version again", agflow("version").stdout.strip() == f"agflow {OLD}")
        check("the user's edit is still there", "MY LOCAL EDIT" in skill.read_text(encoding="utf-8"))
    finally:
        gh.stop()
        if args.keep:
            print(f"kept: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)

    failed = [name for name, ok, _ in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed" + (f"; FAILED: {failed}" if failed else ""))
    return 1 if failed else 0


DRIVER = '''
import json, sys
from agflow import release_feed as rf, update, update_apply

action, base, repo = sys.argv[1:4]
feed = rf.Feed(repo, api=base, allow_loopback=True, token=None)
try:
    if action == "apply":
        plan = update.plan(feed=feed, target=sys.argv[4], allow_breaking=True)
        if not plan.ok:
            print(json.dumps({"ok": False, "blocked": plan.blocked})); raise SystemExit(0)
        out = update_apply.apply(plan, feed=feed)
    else:
        out = update_apply.rollback()
    print(json.dumps(out.to_json()))
except Exception as exc:
    print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
'''

if __name__ == "__main__":
    raise SystemExit(main())
