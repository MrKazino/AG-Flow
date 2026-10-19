"""A fake environment for the updater's tests: a site-packages folder holding an "installed" AG-Flow, wheels to build,
and a command runner that plays the parts of the installer, doctor, ``update refresh`` and ``update verify``.

The plugin installs and the user-edit merge are real (they run in the test process against the sandboxed HOME); only
the replacement of the Python package and the subprocess boundaries are simulated."""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agflow import install as inst
from agflow import paths
from agflow.update import InstallMode
from agflow.update_apply import Proc


def init_text(version: str) -> str:
    return f'"""agflow (fake)"""\n__version__ = "{version}"\n'


def tree(root: Path) -> dict[str, str]:
    """``{relative path: sha256}`` of every file under ``root`` (a missing folder is empty)."""
    out: dict[str, str] = {}
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


class FakeEnv:
    def __init__(self, root: Path, plugin_src: Path, version: str = "0.3.0"):
        self.root = root
        self.plugin_src = plugin_src
        self.site = root / "site"
        self.mode = InstallMode("venv", self.site / "agflow", self.site, "python-fake")
        self.calls: list[list[str]] = []
        # behaviour switches
        self.doctor_errors = 0
        self.install_behaviour = "ok"       # ok | fail | corrupt-then-fail
        self.refresh_fails = False
        self.verify_problems: list[str] = []
        self.verify_version: str | None = None  # None: report what is installed
        self.verify_fails_for: set[str] = set()  # versions whose verification fails (the restored one, say)
        self.raise_at: tuple[str, BaseException] | None = None
        self.install_old(version)

    # -- the installed package --------------------------------------------------------------------
    def install_old(self, version: str) -> None:
        pkg = self.site / "agflow"
        shutil.rmtree(pkg, ignore_errors=True)
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text(init_text(version), encoding="utf-8")
        (pkg / "module.py").write_text(f"VALUE = 'old {version}'\n", encoding="utf-8")
        shutil.copytree(self.plugin_src, pkg / "_plugin")
        info = self.site / f"agflow-{version}.dist-info"
        info.mkdir(parents=True)
        (info / "METADATA").write_text(f"Name: agflow\nVersion: {version}\n", encoding="utf-8")

    @property
    def bundle(self) -> Path:
        return self.site / "agflow" / "_plugin"

    def installed_version(self) -> str:
        text = (self.site / "agflow" / "__init__.py").read_text(encoding="utf-8")
        match = re.search(r'__version__ = "([^"]+)"', text)
        return match.group(1) if match else "?"

    def package_tree(self) -> dict[str, str]:
        return tree(self.site)

    # -- wheels -----------------------------------------------------------------------------------
    def make_wheel(self, version: str, *, mutate: Callable[[dict[str, bytes]], None] | None = None,
                   dist_name: str = "agflow", inner_version: str | None = None,
                   extra: dict[str, bytes] | None = None) -> bytes:
        files: dict[str, bytes] = {
            "agflow/__init__.py": init_text(inner_version or version).encode(),
            "agflow/module.py": f"VALUE = 'new {version}'\n".encode(),
        }
        for path in sorted(self.plugin_src.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                files[f"agflow/_plugin/{path.relative_to(self.plugin_src).as_posix()}"] = path.read_bytes()
        files[f"agflow-{version}.dist-info/METADATA"] = f"Name: {dist_name}\nVersion: {version}\n".encode()
        files[f"agflow-{version}.dist-info/WHEEL"] = b"Wheel-Version: 1.0\n"
        if mutate:
            mutate(files)
        files.update(extra or {})
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in files.items():
                zf.writestr(name, data)
        return buf.getvalue()

    # -- the runner -------------------------------------------------------------------------------
    def runner(self) -> Callable[..., Proc]:
        def run(argv: list[str], env: dict[str, str] | None = None, cwd: Path | None = None,
                timeout: float = 0) -> Proc:
            self.calls.append(list(argv))
            if "doctor" in argv and "--path" in argv:
                return Proc(0 if not self.doctor_errors else 1,
                            json.dumps({"counts": {"error": self.doctor_errors, "warning": 0}}))
            if argv[0] == "fake-install":
                return self._install(Path(argv[1]))
            if argv[:5] == ["python-fake", "-m", "agflow", "update", "refresh"]:
                return self._refresh(argv)
            if argv[:5] == ["python-fake", "-m", "agflow", "update", "verify"]:
                return self._verify()
            return Proc(127, "", f"unexpected command {argv}")
        return run

    def _maybe_raise(self, step: str) -> None:
        if self.raise_at and self.raise_at[0] == step:
            raise self.raise_at[1]

    def _install(self, wheel: Path) -> Proc:
        self._maybe_raise("install")
        if self.install_behaviour == "fail":
            return Proc(1, "", "error: could not install")
        with zipfile.ZipFile(wheel) as zf:
            pkg = self.site / "agflow"
            shutil.rmtree(pkg, ignore_errors=True)
            for old in self.site.glob("agflow-*.dist-info"):
                shutil.rmtree(old)
            if self.install_behaviour == "corrupt-then-fail":
                pkg.mkdir(parents=True)
                (pkg / "__init__.py").write_text("raise SystemExit('half installed')\n", encoding="utf-8")
                return Proc(1, "", "error: interrupted while writing files")
            zf.extractall(self.site)
        return Proc(0)

    def _refresh(self, argv: list[str]) -> Proc:
        self._maybe_raise("refresh")
        if self.refresh_fails:
            return Proc(1, "", "error: installer refused")
        scope = argv[argv.index("--scope") + 1]
        ws = Path(argv[argv.index("--workspace-root") + 1]) if "--workspace-root" in argv else None
        profile = argv[argv.index("--profile") + 1] if "--profile" in argv else None
        inst.install(scope, ws, source=self.bundle, profile=profile, with_hooks="--no-hooks" not in argv,  # type: ignore[arg-type]
                     with_gemini_md="--no-gemini-md" not in argv, allow_duplicate="--allow-duplicate" in argv)
        return Proc(0, json.dumps({"scope": scope}))

    def _verify(self) -> Proc:
        self._maybe_raise("verify")
        version = self.verify_version or self.installed_version()
        problems: list[Any] = list(self.verify_problems)
        if version in self.verify_fails_for:
            problems.append("hooks.broken: the Stop hook crashed")
        return Proc(1 if problems else 0, json.dumps({"version": version, "problems": problems}))


def global_state(extra_skip: tuple[str, ...] = ("update-state.json", "update-journal.json", "update.lock",
                                                "update.json", "incoming")) -> dict[str, str]:
    """Everything AG-Flow keeps under ~/.gemini that an update may change, except the updater's own bookkeeping."""
    home = paths.gemini_dir()
    out = {k: v for k, v in tree(home).items() if not any(part in extra_skip for part in Path(k).parts)
           and ".agflow-backups" not in Path(k).parts and ".tmp" not in Path(k).parts}
    return out
