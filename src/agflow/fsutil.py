"""Small filesystem helpers: atomic writes, JSON/YAML IO, timestamped backups."""

from __future__ import annotations

import errno
import json
import os
import shutil
import stat
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ruamel.yaml import YAML


def atomic_write_text(path: Path, text: str, *, durable: bool = True) -> None:
    """Replace ``path`` with ``text`` so a reader (or a crash) sees the old or the new content, never a mix.

    * a symlink is written *through*: the link survives and its target is replaced (linked dotfiles keep
      working), instead of the link being swapped for a regular file;
    * an existing file keeps its permission bits (POSIX); a new file stays owner-only (``mkstemp``'s 0600);
    * ``durable`` (default) fsyncs the data before the rename and the directory after it, so the rename cannot
      be persisted without the data it points at.
    """
    target = Path(os.path.realpath(path)) if path.is_symlink() else path
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            if durable:
                fh.flush()
                os.fsync(fh.fileno())
        _keep_mode(target, tmp)
        os.replace(tmp, target)
        if durable:
            _fsync_dir(target.parent)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _keep_mode(existing: Path, tmp: str) -> None:
    if os.name == "nt":
        return  # mode bits are advisory on Windows; chmod would only toggle the read-only flag
    try:
        mode = stat.S_IMODE(os.stat(existing).st_mode)
    except FileNotFoundError:
        return  # a new file keeps mkstemp's private 0600
    os.chmod(tmp, mode)


# errno values meaning "this filesystem cannot fsync a directory" (some network and overlay filesystems)
_DIR_FSYNC_UNSUPPORTED = {errno.EINVAL, errno.ENOTSUP, errno.EBADF}


def _fsync_dir(directory: Path) -> None:
    if os.name == "nt":
        return  # a directory cannot be opened for fsync on Windows
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    except OSError as exc:
        if exc.errno not in _DIR_FSYNC_UNSUPPORTED:
            raise
    finally:
        os.close(fd)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return default
    return json.loads(text)


def write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def yaml_rt() -> YAML:
    from ruamel.yaml import YAML

    y = YAML(typ="rt")
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def yaml_safe() -> YAML:
    from ruamel.yaml import YAML

    return YAML(typ="safe", pure=True)


def read_yaml(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    data = yaml_safe().load(path.read_text(encoding="utf-8"))
    return default if data is None else data


def write_yaml(path: Path, data: Any) -> None:
    from io import StringIO

    buf = StringIO()
    yaml_rt().dump(data, buf)
    atomic_write_text(path, buf.getvalue())


def timestamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime()) + f"-{int(time.time() * 1000) % 1000:03d}"


def backup(path: Path, backup_root: Path, anchor: Path) -> Path | None:
    """Copy ``path`` (file or dir) into ``backup_root`` preserving its location relative to ``anchor``."""
    if not path.exists():
        return None
    try:
        rel = path.resolve().relative_to(anchor.resolve())
    except ValueError:
        rel = Path(path.name)
    dest = backup_root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if path.is_dir():
        shutil.copytree(path, dest, dirs_exist_ok=True, symlinks=True)
    else:
        shutil.copy2(path, dest)
    return dest


def unique_sibling(path: Path, suffix: str = ".bak") -> Path:
    """A name next to ``path`` that is not taken: ``x.md.bak``, else ``x.md.bak2``, ``x.md.bak3``, ...

    Renaming onto an existing file raises on Windows and silently replaces it on POSIX; either way an earlier
    backup is lost or the operation crashes. Choosing a free name makes both platforms behave the same."""
    candidate = path.with_name(path.name + suffix)
    n = 2
    while candidate.exists() or candidate.is_symlink():  # is_symlink: a dangling link still occupies the name
        candidate = path.with_name(f"{path.name}{suffix}{n}")
        n += 1
    return candidate


def remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def copy_tree(src: Path, dest: Path, ignore: tuple[str, ...] = ("__pycache__", "*.pyc", ".DS_Store")) -> None:
    shutil.copytree(src, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns(*ignore))
