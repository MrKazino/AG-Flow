"""YAML frontmatter parsing and round-trip editing for Markdown components."""

from __future__ import annotations

import re
from io import StringIO
from typing import Any

from ruamel.yaml.error import YAMLError

from agflow.fsutil import yaml_rt

_FENCE = re.compile(r"^---[ \t]*\r?\n(.*?)(?:\r?\n)?^---[ \t]*(?:\r?\n|$)", re.DOTALL | re.MULTILINE)


class FrontmatterError(ValueError):
    pass


def split(text: str) -> tuple[str | None, str]:
    """Return (raw YAML or None, body). Frontmatter must start on the first line."""
    text = text.lstrip("﻿")
    if not text.startswith("---"):
        return None, text
    m = _FENCE.match(text)
    if not m:
        return None, text
    return m.group(1), text[m.end() :]


def parse(text: str) -> tuple[dict[str, Any], str]:
    """Parse frontmatter into a plain dict. Missing frontmatter yields ``{}``."""
    raw, body = split(text)
    if raw is None:
        return {}, body
    try:
        data = yaml_rt().load(raw) if raw.strip() else {}
    except YAMLError as exc:
        raise FrontmatterError(f"invalid YAML frontmatter: {exc}") from exc
    if data is None:
        return {}, body
    if not isinstance(data, dict):
        raise FrontmatterError("frontmatter must be a YAML mapping")
    return _plain(data), body


def has_frontmatter(text: str) -> bool:
    return split(text)[0] is not None


def dump(meta: dict[str, Any], body: str) -> str:
    buf = StringIO()
    yaml_rt().dump(meta, buf)
    body = body if body.startswith("\n") or not body else "\n" + body
    return f"---\n{buf.getvalue()}---{body}"


def update(text: str, changes: dict[str, Any], remove: tuple[str, ...] = ()) -> str:
    """Change frontmatter keys while preserving comments, key order and the body."""
    raw, body = split(text)
    y = yaml_rt()
    data = (y.load(raw) if raw and raw.strip() else None) or {}
    if not isinstance(data, dict):
        raise FrontmatterError("frontmatter must be a YAML mapping")
    for key, value in changes.items():
        data[key] = value
    for key in remove:
        data.pop(key, None)
    buf = StringIO()
    y.dump(data, buf)
    if raw is None:
        return f"---\n{buf.getvalue()}---" + ("\n" + body if body and not body.startswith("\n") else body)
    return f"---\n{buf.getvalue()}---\n{body}"  # the closing fence's newline was consumed by split()


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, str):
        return str(value)
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    return value
