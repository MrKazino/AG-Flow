"""Install is crash-safe (F6): back up -> record -> act, and the plugin directory is swapped atomically.

A hard crash is simulated with a ``BaseException`` raised at a chosen step: unlike an ``OSError`` it triggers no
rollback code, which is exactly what a power loss or ``kill -9`` looks like to the files left behind.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from agflow import install as inst
from agflow import paths
from agflow.install import InstallError, install, recover_interrupted, uninstall
from agflow.inventory import Inventory
from agflow.validate import validate_inventory
from tests.conftest import write


class Crash(BaseException):
    """A hard stop. Nothing that handles ``Exception``/``OSError`` runs, as with a real crash."""


def tree(root: Path, skip: tuple[Path, ...]) -> dict[str, str]:
    """Every file under ``root`` (except ``skip``) with a content hash: the tree 'as the user would see it'."""
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_dir() or any(s == p or s in p.parents for s in skip):
            continue
        out[p.relative_to(root).as_posix()] = hashlib.sha1(p.read_bytes()).hexdigest()
    return out


def agflow_state(home: Path) -> tuple[Path, ...]:
    """agflow's own bookkeeping, which is allowed to differ between 'before install' and 'after uninstall'."""
    return (home / ".gemini" / "config" / "agflow", home / ".gemini" / "config" / ".agflow-backups")


@pytest.fixture
def lived_in(home: Path) -> Path:
    """A home that has history: the user's GEMINI.md and an older copy of the plugin."""
    md = home / ".gemini" / "GEMINI.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_bytes(b"# Mine\n- Prefer pnpm.\n")  # explicit LF: write_text would produce CRLF on Windows
    old = home / ".gemini" / "config" / "plugins" / "agflow"
    write(old / "plugin.json", json.dumps({"name": "agflow", "version": "0.0.1", "description": "older copy"}))
    write(old / "old.txt", "old")
    return home


def tmp_dir() -> Path:
    return paths.install_tmp_dir(paths.global_state_dir())


def arm(monkeypatch: pytest.MonkeyPatch, point: str, target: Path) -> None:
    """Make ``install`` hard-crash at ``point``."""
    real_replace, real_remove = os.replace, inst.remove
    real_write_text, real_write_json = inst.atomic_write_text, inst.write_json
    state = {"gemini_written": False}

    def boom(*a: object, **k: object) -> None:
        raise Crash(point)

    if point == "stage_build":
        monkeypatch.setattr(inst, "copy_tree", boom)
    elif point == "swap_second_rename":  # the live plugin was already moved aside, the new one not yet in place
        monkeypatch.setattr(os, "replace", lambda s, d: boom() if Path(d) == target else real_replace(s, d))
    elif point == "delete_old_copy":  # swapped, the superseded copy not yet deleted
        monkeypatch.setattr(inst, "remove", lambda p: boom() if Path(p).name.endswith("-old") else real_remove(p))
    elif point == "gemini_md_write":
        monkeypatch.setattr(inst, "atomic_write_text",
                            lambda p, t, **k: boom() if Path(p).name == "GEMINI.md" else real_write_text(p, t, **k))
    elif point == "final_manifest_write":
        def track(p: Path, text: str, **k: object) -> None:
            real_write_text(p, text, **k)  # type: ignore[arg-type]
            state["gemini_written"] = state["gemini_written"] or Path(p).name == "GEMINI.md"

        monkeypatch.setattr(inst, "atomic_write_text", track)
        def manifest_write(p: Path, d: object) -> None:
            if state["gemini_written"] and Path(p).name == "install.json":
                boom()
            real_write_json(p, d)

        monkeypatch.setattr(inst, "write_json", manifest_write)
    else:  # pragma: no cover
        raise AssertionError(point)


def hard_crash(monkeypatch: pytest.MonkeyPatch, point: str) -> None:
    """Run ``install("global")`` and crash it at ``point``. Patches are scoped, so the HOME sandbox stays in place."""
    target = paths.global_layout().plugins_dir / "agflow"
    with monkeypatch.context() as mp:
        arm(mp, point, target)
        with pytest.raises(Crash):
            install("global")


CRASH_POINTS = ["stage_build", "swap_second_rename", "delete_old_copy", "gemini_md_write", "final_manifest_write"]


@pytest.mark.parametrize("point", CRASH_POINTS)
def test_a_crash_at_any_step_is_recoverable_and_uninstall_restores_the_original_tree(lived_in, monkeypatch, point):
    before = tree(lived_in, agflow_state(lived_in))
    target = paths.global_layout().plugins_dir / "agflow"
    hard_crash(monkeypatch, point)

    # a complete plugin still exists somewhere recoverable, and the manifest already records the backup
    assert (target / "plugin.json").exists() or any((p / "plugin.json").exists() for p in tmp_dir().glob("*-old"))
    manifest = json.loads(inst._manifest_path("global", None).read_text(encoding="utf-8"))
    assert str(target) in manifest["modified"], "the backup of the replaced plugin must be journalled first"
    assert Path(manifest["modified"][str(target)]).exists()

    recover_interrupted("global", None)
    assert not tmp_dir().exists() or not any(tmp_dir().iterdir()), "debris from the interrupted install is gone"
    uninstall("global")
    assert tree(lived_in, agflow_state(lived_in)) == before


@pytest.mark.parametrize("point", CRASH_POINTS)
def test_installing_again_after_a_crash_completes_it(lived_in, monkeypatch, point):
    before = tree(lived_in, agflow_state(lived_in))
    hard_crash(monkeypatch, point)
    install("global")  # recovers by itself first
    target = paths.global_layout().plugins_dir / "agflow"
    assert (target / "plugin.json").is_file() and not (target / "old.txt").exists()
    assert not tmp_dir().exists() or not any(tmp_dir().iterdir())
    assert not [i for i in validate_inventory(Inventory.discover(None)) if i.level.value != "info"]
    uninstall("global")
    assert tree(lived_in, agflow_state(lived_in)) == before


def test_the_manifest_is_on_disk_before_the_old_plugin_is_touched(lived_in, monkeypatch):
    """The core of F6: previously the manifest (the only record of the backup) was written last."""
    seen: dict[str, object] = {}

    def look_then_crash(src: Path, dest: Path, *a: object, **k: object) -> None:
        manifest_file = inst._manifest_path("global", None)
        seen["manifest_exists"] = manifest_file.is_file()
        seen["old_plugin_untouched"] = (paths.global_layout().plugins_dir / "agflow" / "old.txt").is_file()
        raise Crash

    monkeypatch.setattr(inst, "copy_tree", look_then_crash)
    with pytest.raises(Crash):
        install("global")
    assert seen == {"manifest_exists": True, "old_plugin_untouched": True}


def test_a_failure_while_building_leaves_the_live_plugin_alone(lived_in, monkeypatch):
    def disk_full(*a: object, **k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(inst, "copy_tree", disk_full)
    with pytest.raises(OSError, match="disk full"):
        install("global")
    assert (paths.global_layout().plugins_dir / "agflow" / "old.txt").read_text() == "old"
    assert not tmp_dir().exists() or not any(tmp_dir().iterdir()), "the half-built copy is cleaned up"


def test_a_locked_plugin_directory_keeps_the_old_plugin_and_explains(lived_in, monkeypatch):
    target = paths.global_layout().plugins_dir / "agflow"
    real = os.replace

    def locked(src, dst):
        if Path(src) == target:  # e.g. Windows: Antigravity has files in the plugin open
            raise PermissionError(5, "Access is denied")
        return real(src, dst)

    monkeypatch.setattr(os, "replace", locked)
    with pytest.raises(InstallError, match="in use"):
        install("global")
    assert (target / "old.txt").read_text() == "old"
    assert not tmp_dir().exists() or not any(tmp_dir().iterdir())


def test_if_the_new_plugin_cannot_be_moved_in_the_old_one_is_put_back(lived_in, monkeypatch):
    target = paths.global_layout().plugins_dir / "agflow"
    real = os.replace

    def second_fails(src, dst):
        if Path(dst) == target and Path(src).name.endswith("-new"):  # only moving the new plugin in; not the rollback
            raise OSError(28, "no space left on device")
        return real(src, dst)

    monkeypatch.setattr(os, "replace", second_fails)
    with pytest.raises(OSError, match="no space"):
        install("global")
    assert (target / "old.txt").read_text() == "old"


def test_profile_with_link_is_refused_before_anything_is_touched(lived_in):
    """Previously the existing plugin was deleted first and the incompatible flags were noticed afterwards."""
    target = paths.global_layout().plugins_dir / "agflow"
    with pytest.raises(InstallError, match="--link cannot be combined with --profile"):
        install("global", link=True, profile="core")
    assert (target / "old.txt").read_text() == "old"


def test_recover_is_a_noop_without_debris(home):
    assert recover_interrupted("global", None) == []


def test_recover_puts_back_a_plugin_that_was_moved_aside(lived_in, monkeypatch):
    target = paths.global_layout().plugins_dir / "agflow"
    hard_crash(monkeypatch, "swap_second_rename")
    assert not target.exists()  # the window F6 is about: moved aside, new one not in place
    actions = recover_interrupted("global", None)
    assert (target / "old.txt").read_text() == "old"
    assert any("restored" in a for a in actions)


def test_doctor_reports_an_interrupted_install_until_it_is_recovered(lived_in, monkeypatch):
    hard_crash(monkeypatch, "swap_second_rename")
    issues = validate_inventory(Inventory.discover(None))
    found = [i for i in issues if i.code == "install.interrupted"]
    assert found and found[0].level.value == "warning" and "agflow install" in found[0].message
    recover_interrupted("global", None)
    assert not [i for i in validate_inventory(Inventory.discover(None)) if i.code == "install.interrupted"]


def test_debris_is_never_discovered_as_a_second_plugin(lived_in, monkeypatch):
    hard_crash(monkeypatch, "swap_second_rename")
    names = [i.path for i in Inventory.discover(None).of(inst_kind())]
    assert not [p for p in names if "install-tmp" in str(p)]


def inst_kind():
    from agflow.models import Kind

    return Kind.PLUGIN


@pytest.mark.parametrize("profile", [None, "core"])
def test_install_then_uninstall_leaves_the_tree_identical_global(lived_in, profile):
    before = tree(lived_in, agflow_state(lived_in))
    install("global", profile=profile)
    assert tree(lived_in, agflow_state(lived_in)) != before
    uninstall("global")
    assert tree(lived_in, agflow_state(lived_in)) == before


@pytest.mark.parametrize("profile", [None, "frontend"])
def test_install_then_uninstall_leaves_the_tree_identical_workspace(home, workspace, profile):
    write(workspace / ".agents" / "rules" / "mine.md", "---\ntrigger: manual\n---\nmine\n")
    state = (workspace / ".agents" / "agflow",)
    before_ws, before_home = tree(workspace, state), tree(home, agflow_state(home))
    install("workspace", workspace, profile=profile)
    uninstall("workspace", workspace)
    assert tree(workspace, state) == before_ws and tree(home, agflow_state(home)) == before_home
