"""Filesystem primitives: atomic writes (F14) and backups (F8, F15)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from agflow import fsutil

posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits / directory fsync")


# --- F14: atomic_write_text -------------------------------------------------------------------------------------


@pytest.fixture
def fsync_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record what each fsync call targeted ("file" or "dir") while still performing it."""
    calls: list[str] = []
    real = os.fsync

    def spy(fd: int) -> None:
        calls.append("dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        real(fd)

    monkeypatch.setattr(os, "fsync", spy)
    return calls


def test_file_is_fsynced_before_the_rename(tmp_path: Path, fsync_calls: list[str], monkeypatch: pytest.MonkeyPatch):
    order: list[str] = []
    real_replace = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (order.append(f"replace after {len(fsync_calls)} fsync"),
                                                     real_replace(a, b))[1])
    fsutil.atomic_write_text(tmp_path / "f.txt", "data")
    assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "data"
    assert fsync_calls[0] == "file"
    assert order == ["replace after 1 fsync"]  # the file was already durable when it became visible


@posix_only
def test_parent_directory_is_fsynced_after_the_rename(tmp_path: Path, fsync_calls: list[str]):
    fsutil.atomic_write_text(tmp_path / "f.txt", "data")
    assert fsync_calls == ["file", "dir"]


def test_durable_false_skips_fsync(tmp_path: Path, fsync_calls: list[str]):
    fsutil.atomic_write_text(tmp_path / "f.txt", "data", durable=False)
    assert fsync_calls == []


def test_symlinked_dotfile_stays_a_symlink(tmp_path: Path, require_symlinks: None):
    real = tmp_path / "dotfiles" / "gitconfig"
    real.parent.mkdir()
    real.write_text("old", encoding="utf-8")
    link = tmp_path / ".gitconfig"
    os.symlink(real, link)
    fsutil.atomic_write_text(link, "new")
    assert link.is_symlink(), "the link must survive the write"
    assert real.read_text(encoding="utf-8") == "new", "the write goes through to the link's target"
    assert not [p for p in real.parent.iterdir() if p != real], "no temp file left next to the target"


def test_dangling_symlink_creates_its_target(tmp_path: Path, require_symlinks: None):
    link = tmp_path / "link.json"
    os.symlink(tmp_path / "later" / "target.json", link)
    fsutil.atomic_write_text(link, "{}")
    assert link.is_symlink() and (tmp_path / "later" / "target.json").read_text(encoding="utf-8") == "{}"


@posix_only
def test_existing_file_mode_is_preserved(tmp_path: Path):
    f = tmp_path / "script.sh"
    f.write_text("old", encoding="utf-8")
    os.chmod(f, 0o755)
    fsutil.atomic_write_text(f, "new")
    assert stat.S_IMODE(f.stat().st_mode) == 0o755


@posix_only
def test_new_file_stays_private(tmp_path: Path):
    """Deliberately unchanged: configs may hold env values, so new files remain owner-only."""
    f = tmp_path / "fresh.json"
    fsutil.atomic_write_text(f, "{}")
    assert stat.S_IMODE(f.stat().st_mode) == 0o600


def test_failed_replace_leaves_original_and_no_temp_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    work = tmp_path / "work"
    work.mkdir()
    f = work / "keep.txt"
    f.write_text("original", encoding="utf-8")

    def boom(src: str, dst: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        fsutil.atomic_write_text(f, "new")
    assert f.read_text(encoding="utf-8") == "original"
    assert [p.name for p in work.iterdir()] == ["keep.txt"]


def test_parent_directories_are_created(tmp_path: Path):
    fsutil.atomic_write_text(tmp_path / "a" / "b" / "c.txt", "x")
    assert (tmp_path / "a" / "b" / "c.txt").read_text(encoding="utf-8") == "x"


def test_json_and_yaml_writers_inherit_the_behaviour(tmp_path: Path, fsync_calls: list[str]):
    fsutil.write_json(tmp_path / "d.json", {"a": 1})
    fsutil.write_yaml(tmp_path / "d.yaml", {"a": 1})
    assert fsync_calls.count("file") == 2


# --- F8: a backup name that never collides --------------------------------------------------------------------------


def test_unique_sibling_walks_bak_bak2_bak3(tmp_path: Path):
    f = tmp_path / "ship.md"
    f.write_text("wf", encoding="utf-8")
    assert fsutil.unique_sibling(f) == tmp_path / "ship.md.bak"
    (tmp_path / "ship.md.bak").write_text("1", encoding="utf-8")
    assert fsutil.unique_sibling(f) == tmp_path / "ship.md.bak2"
    (tmp_path / "ship.md.bak2").write_text("2", encoding="utf-8")
    assert fsutil.unique_sibling(f) == tmp_path / "ship.md.bak3"
    assert fsutil.unique_sibling(f, ".orig") == tmp_path / "ship.md.orig"  # other suffixes are independent


def test_unique_sibling_treats_a_dangling_symlink_as_taken(tmp_path: Path, require_symlinks: None):
    f = tmp_path / "ship.md"
    f.write_text("wf", encoding="utf-8")
    os.symlink(tmp_path / "nowhere", tmp_path / "ship.md.bak")  # exists() is False for a dangling link
    assert fsutil.unique_sibling(f) == tmp_path / "ship.md.bak2"
