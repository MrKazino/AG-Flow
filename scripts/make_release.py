#!/usr/bin/env python3
"""Build the release artifacts the updater understands, and check them the way a client will.

    python scripts/make_release.py [--out dist/release] [--min-antigravity 1.3.0] [--breaking] [--notes TEXT]

Writes to ``--out`` (emptied first, only if it is inside this repository's ``dist/``):

* ``agflow-<version>-py3-none-any.whl`` and ``agflow-<version>.tar.gz``
* ``SHA256SUMS``  (``sha256sum`` format; the updater refuses a wheel that is not listed and matching)
* ``release.json`` (``version``, ``tested_antigravity``, ``python``, and ``min_antigravity`` / ``breaking`` / ``notes``
  when given: what the updater checks before it offers the release to a machine)

Then it re-checks the result like ``agflow update apply`` would: the three version numbers agree, the wheel passes the
same inspection and the sums match. It never commits, tags, pushes or publishes; it prints the command to do that.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


class ReleaseError(RuntimeError):
    """The release cannot be built or does not check out; the message says why."""


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
                          stdin=subprocess.DEVNULL, timeout=900)


def read_versions(root: Path = ROOT) -> dict[str, str]:
    """The version written in the package, in pyproject.toml and in the plugin manifest; they must agree."""
    init = re.search(r'__version__ = "([^"]+)"', (root / "src" / "agflow" / "__init__.py").read_text(encoding="utf-8"))
    pyproject = re.search(r'(?m)^version = "([^"]+)"', (root / "pyproject.toml").read_text(encoding="utf-8"))
    try:
        plugin = json.loads((root / "plugin" / "plugin.json").read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError):
        plugin = None
    found = {"agflow/__init__.py": init.group(1) if init else None,
             "pyproject.toml": pyproject.group(1) if pyproject else None, "plugin/plugin.json": plugin}
    if None in found.values() or len(set(found.values())) != 1:
        raise ReleaseError("the version differs between files (they must all say the same): "
                           + ", ".join(f"{k} = {v}" for k, v in found.items()))
    return {k: str(v) for k, v in found.items()}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def sums_text(files: list[Path]) -> str:
    """``sha256sum`` output for ``files`` (sorted by name, two spaces, LF line endings)."""
    return "".join(f"{sha256_file(p)}  {p.name}\n" for p in sorted(files, key=lambda p: p.name))


def release_meta(version: str, *, python_spec: str, tested: list[str], min_antigravity: str | None = None,
                 breaking: bool = False, notes: str = "") -> dict[str, object]:
    meta: dict[str, object] = {"version": version, "tested_antigravity": tested, "python": python_spec}
    if min_antigravity:
        meta["min_antigravity"] = min_antigravity
    if breaking:
        meta["breaking"] = True
    if notes:
        meta["notes"] = notes[:2000]
    return meta


def build(root: Path, out: Path, run: Runner = _run) -> list[Path]:
    """``uv build`` of the wheel and the sdist into ``out``."""
    if out.exists():
        shutil.rmtree(out)
    done = run(["uv", "build", "--out-dir", str(out), str(root)])
    if done.returncode != 0:
        raise ReleaseError(f"uv build failed:\n{done.stdout}\n{done.stderr}")
    wheels, sdists = sorted(out.glob("agflow-*.whl")), sorted(out.glob("agflow-*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ReleaseError(f"expected one wheel and one sdist in {out}, found {[p.name for p in wheels + sdists]}")
    return [wheels[0], sdists[0]]


def verify(out: Path, version: str) -> None:
    """Re-check ``out`` the way a client does: the wheel passes inspection and every file matches SHA256SUMS."""
    from agflow.release_feed import parse_sums
    from agflow.update_apply import UpdateError, inspect_wheel

    wheel = next(iter(sorted(out.glob("agflow-*.whl"))), None)
    if wheel is None:
        raise ReleaseError("there is no wheel")
    try:
        inspect_wheel(wheel, version)
    except UpdateError as exc:
        raise ReleaseError(f"the wheel would be refused by the updater: {exc}") from None
    listed = parse_sums((out / "SHA256SUMS").read_text(encoding="utf-8"))
    for path in sorted(out.glob("agflow-*")):  # not uv's own .gitignore, not SHA256SUMS or release.json
        if listed.get(path.name) != sha256_file(path):
            raise ReleaseError(f"SHA256SUMS does not match {path.name}")
    meta = json.loads((out / "release.json").read_text(encoding="utf-8"))
    if meta.get("version") != version:
        raise ReleaseError("release.json names a different version")


def tested_antigravity(root: Path = ROOT) -> list[str]:
    try:
        data = json.loads((root / "src" / "agflow" / "data" / "compat_baseline.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [v for v in data.get("tested_antigravity", []) if isinstance(v, str)]


def requires_python(root: Path = ROOT) -> str:
    match = re.search(r'(?m)^requires-python = "([^"]+)"', (root / "pyproject.toml").read_text(encoding="utf-8"))
    return match.group(1) if match else ""


def make(root: Path, out: Path, *, min_antigravity: str | None = None, breaking: bool = False, notes: str = "",
         run: Runner = _run) -> Path:
    versions = read_versions(root)
    version = next(iter(versions.values()))
    built = build(root, out, run)
    (out / "SHA256SUMS").write_text(sums_text(built), encoding="utf-8", newline="\n")
    meta = release_meta(version, python_spec=requires_python(root), tested=tested_antigravity(root),
                        min_antigravity=min_antigravity, breaking=breaking, notes=notes)
    (out / "release.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8", newline="\n")
    verify(out, version)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "release")
    parser.add_argument("--min-antigravity", help="oldest Antigravity this release supports (e.g. 1.3.0)")
    parser.add_argument("--breaking", action="store_true", help="mark the release as containing breaking changes")
    parser.add_argument("--notes", default="", help="a short note shown with the plan (max 2000 characters)")
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if ROOT / "dist" not in out.parents:
        print(f"refusing to empty {out}: --out must be inside {ROOT / 'dist'}", file=sys.stderr)
        return 2
    try:
        make(ROOT, out, min_antigravity=args.min_antigravity, breaking=args.breaking, notes=args.notes)
    except ReleaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    version = next(iter(read_versions().values()))
    print(f"release {version} built and checked in {out}:")
    for path in sorted(out.iterdir()):
        print(f"  {path.name}  ({path.stat().st_size // 1024} KB)")
    print("\nWhen you are ready (this script never commits, tags or publishes):")
    print(f"  git tag v{version} && git push origin v{version}      # the Release workflow builds and publishes it")
    print(f"  or: gh release create v{version} {out}/* --title \"AG-Flow {version}\" "
          f"--notes-file RELEASE-NOTES-{version}.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
