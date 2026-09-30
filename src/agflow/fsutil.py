"""Small filesystem helpers: atomic writes, JSON/YAML IO, timestamped backups."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ruamel.yaml import YAML


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


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


def remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def copy_tree(src: Path, dest: Path, ignore: tuple[str, ...] = ("__pycache__", "*.pyc", ".DS_Store")) -> None:
    shutil.copytree(src, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns(*ignore))
