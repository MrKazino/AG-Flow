"""A read-only look at the Antigravity installation AG-Flow runs inside: which versions are installed, and what the
documentation Antigravity ships says about the interfaces AG-Flow depends on (hooks, plugins, sidecars, configs).

Google changes those interfaces between releases. Instead of guessing, AG-Flow fingerprints the shipped docs and pulls
out the *names* they define (hook events, decisions, manifest fields), so :mod:`agflow.compat` can say exactly what
moved. Only hashes and short identifiers are kept, never vendor text.

Everything here is offline, bounded (size and time caps) and total: a missing installation is a normal answer, not an
exception. Nothing is written, and nothing in the installation is run except ``<tool> --version``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any

from agflow import paths

# Product folders under ~/.gemini that can hold the shipped skills (desktop app, CLI, IDE), in order of preference.
PRODUCT_DIRS = ("antigravity", "antigravity-cli", "antigravity-ide")
SKILLS_SUBPATH = ("builtin", "skills")

# The shipped docs that define what AG-Flow builds on, relative to the skills folder.
TRACKED_DOCS = (
    "agy-customizations/docs/hooks.md",
    "agy-customizations/docs/plugins.md",
    "agy-customizations/docs/json_configs.md",
    "agy-customizations/docs/mcp_servers.md",
    "agy-customizations/docs/skills.md",
    "agy-customizations/docs/rules.md",
    "plugin/SKILL.md",
    "ui-plugin-navigation/SKILL.md",
    "automation/SKILL.md",
)
MARKER_KINDS = ("fields", "table_keys", "values", "json_keys", "code_terms", "layouts", "limits", "tree_files")
MAX_DOC_BYTES = 512 * 1024
MAX_MARKERS = 300
MAX_CODE_TERMS = 800  # inline-code words are the broadest kind: every `word` a doc mentions
VERSION_TIMEOUT = 5.0

_FIELD = re.compile(r"^[ \t]*[*-][ \t]+\*\*`([^`\n]+)`\*\*", re.M)
_TABLE_KEY = re.compile(r"^\|[ \t]*`([^`\n|]+)`[ \t]*\|", re.M)
_VALUE = re.compile(r'^[ \t]*[*-][ \t]+`"([^"`\n]+)"`', re.M)
_CODE = re.compile(r'`"?([A-Za-z][A-Za-z0-9_.\-]{1,60})"?`')
_LIMIT = re.compile(r"`(\d[\d,._ ]{0,12}[A-Za-z]{0,3})`")
_TREE = re.compile(r"[\u251c\u2514]\u2500\u2500[ \t]+([^\s#]+)")  # "|-- name" / "`-- name" in a drawn tree
_KEY_TOKEN = re.compile(r'"([A-Za-z][A-Za-z0-9_]{1,40})"[ \t]*:')
_FENCE = re.compile(r"```[A-Za-z]*[ \t]*\n(.*?)```", re.S)
_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]{1,40}")
_VERSION = re.compile(r"\d+(?:\.\d+){1,3}(?:[-.]?(?:alpha|beta|dev|rc|a|b)\.?\d*)?")


@dataclass
class Snapshot:
    """What was found. Every field has a safe empty value, so a partial answer is still a usable one."""

    agy_version: str | None = None
    agy_path: str | None = None
    skills_root: str | None = None
    doc_hashes: dict[str, str] = field(default_factory=dict)
    markers: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    builtin_skills: list[str] = field(default_factory=list)
    config_keys: list[str] = field(default_factory=list)
    gemini_cli_version: str | None = None
    sdk_version: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def has_docs(self) -> bool:
        return bool(self.doc_hashes)

    def to_json(self) -> dict[str, Any]:
        return {"agy_version": self.agy_version, "skills_root": self.skills_root, "doc_hashes": self.doc_hashes,
                "markers": self.markers, "builtin_skills": self.builtin_skills, "config_keys": self.config_keys,
                "gemini_cli_version": self.gemini_cli_version, "sdk_version": self.sdk_version}

    @classmethod
    def from_json(cls, data: Any) -> Snapshot:
        """Rebuild a snapshot from stored JSON; anything malformed becomes empty rather than an error."""
        if not isinstance(data, dict):
            return cls()
        hashes = data.get("doc_hashes")
        markers = data.get("markers")
        return cls(
            agy_version=_opt_str(data.get("agy_version")), skills_root=_opt_str(data.get("skills_root")),
            doc_hashes={k: v for k, v in hashes.items() if isinstance(k, str) and isinstance(v, str)}
            if isinstance(hashes, dict) else {},
            markers={doc: {kind: _strs(vals) for kind, vals in kinds.items() if isinstance(kind, str)}
                     for doc, kinds in markers.items() if isinstance(doc, str) and isinstance(kinds, dict)}
            if isinstance(markers, dict) else {},
            builtin_skills=_strs(data.get("builtin_skills")), config_keys=_strs(data.get("config_keys")),
            gemini_cli_version=_opt_str(data.get("gemini_cli_version")), sdk_version=_opt_str(data.get("sdk_version")))


def _opt_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _strs(value: Any) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


# --- marker extraction (pure) ----------------------------------------------------------------------------------------


def _strip_json_comments(block: str) -> str:
    """Remove ``// ...`` comments that sit outside string literals."""
    out: list[str] = []
    in_string = escaped = False
    i = 0
    while i < len(block):
        ch = block[i]
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
            out.append(ch)
        elif ch == "/" and block[i + 1:i + 2] == "/":
            while i < len(block) and block[i] != "\n":
                i += 1
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _json_keys(text: str) -> set[str]:
    """Key names used in the doc's JSON examples. Examples that parse are walked; one that does not (a comment
    containing quotes, a fence that runs on into prose) still yields its ``"key":`` tokens."""
    found: set[str] = set()

    def walk(node: Any, depth: int) -> None:
        if depth > 6:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                if _KEY.fullmatch(key):
                    found.add(key)
                walk(value, depth + 1)
        elif isinstance(node, list):
            for item in node:
                walk(item, depth + 1)

    for block in _FENCE.findall(text):
        cleaned = "\n".join(ln for ln in block.splitlines() if not ln.lstrip().startswith("..."))
        cleaned = re.sub(r",(\s*[}\]])", r"\1", _strip_json_comments(cleaned))
        try:
            walk(json.loads(cleaned), 0)
        except ValueError:
            found.update(_KEY_TOKEN.findall(cleaned))
    return found


def _tree_files(text: str) -> set[str]:
    """Names drawn in directory-tree blocks (``plugin.json``, ``skills/``)."""
    return {name for block in _FENCE.findall(text) for name in _TREE.findall(block)}


def _layouts(text: str) -> set[str]:
    """``Event=grouped`` / ``Event=flat`` for each row of a table whose later columns say how its handlers are laid out
    (rows may wrap onto ``:`` continuation lines)."""
    rows: list[list[str]] = []
    current: list[str] | None = None
    for line in text.splitlines():
        match = _TABLE_KEY.match(line)
        if match:
            current = [match.group(1), line]
            rows.append(current)
        elif current is not None and line.startswith(":"):
            current[1] += " " + line
        else:
            current = None
    found: set[str] = set()
    for name, blob in rows:
        low = blob.lower()
        spots = {word: low.find(word) for word in ("grouped", "flat") if word in low}
        if spots:
            found.add(f"{name}={min(spots, key=spots.__getitem__)}")
    return found


def markers_of(text: str) -> dict[str, list[str]]:
    """The identifiers a doc defines, by kind: bold field bullets, table first columns, quoted-value bullets, the key
    names of its JSON examples, every inline-code word it mentions (outside fenced blocks), and which events are
    grouped or flat, the numeric limits written as inline code (``24,000``), and the names in drawn directory trees."""
    sets = {
        "fields": set(_FIELD.findall(text)),
        "table_keys": set(_TABLE_KEY.findall(text)),
        "values": set(_VALUE.findall(text)),
        "json_keys": _json_keys(text),
        "code_terms": set(_CODE.findall(_FENCE.sub("", text))),
        "layouts": _layouts(text),
        "limits": set(_LIMIT.findall(_FENCE.sub("", text))),
        "tree_files": _tree_files(text),
    }
    return {kind: sorted(sets[kind])[: MAX_CODE_TERMS if kind == "code_terms" else MAX_MARKERS]
            for kind in MARKER_KINDS}


def fingerprint_text(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


# --- reading the installation (bounded, total) -----------------------------------------------------------------------


def find_skills_root(gemini_dir: Path) -> Path | None:
    """The shipped skills folder to read: the first product folder holding the hooks doc, else the first that exists."""
    roots = [gemini_dir.joinpath(product, *SKILLS_SUBPATH) for product in PRODUCT_DIRS]
    for root in roots:
        if (root / TRACKED_DOCS[0]).is_file():
            return root
    return next((r for r in roots if r.is_dir()), None)


def read_docs(root: Path) -> tuple[dict[str, str], dict[str, dict[str, list[str]]], list[str]]:
    hashes: dict[str, str] = {}
    markers: dict[str, dict[str, list[str]]] = {}
    notes: list[str] = []
    for rel in TRACKED_DOCS:
        path = root / rel
        try:
            if not path.is_file():
                continue
            if path.stat().st_size > MAX_DOC_BYTES:
                notes.append(f"{rel} is larger than {MAX_DOC_BYTES // 1024} KB; it was not read")
                continue
            text = path.read_bytes().decode("utf-8", errors="replace")
        except OSError as exc:
            notes.append(f"could not read {rel} ({type(exc).__name__})")
            continue
        hashes[rel] = fingerprint_text(text)
        markers[rel] = markers_of(text)
    return hashes, markers, notes


def find_agy() -> list[str] | None:
    """Command prefix for the Antigravity CLI: on PATH, else the per-user install folder."""
    on_path = shutil.which("agy")
    if on_path:
        return [on_path]
    local = os.environ.get("LOCALAPPDATA")
    candidates = [Path(local) / "agy" / "bin" / "agy.exe"] if local else []
    candidates.append(paths.home() / ".local" / "bin" / "agy")
    return next(([str(c)] for c in candidates if c.is_file()), None)


def tool_version(command: Sequence[str], timeout: float = VERSION_TIMEOUT) -> str | None:
    """The version a tool prints for ``--version``, or None (missing, slow, silent). Never raises."""
    try:
        done = subprocess.run([*command, "--version"], capture_output=True, text=True, timeout=timeout, check=False,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    match = _VERSION.search(f"{done.stdout or ''}\n{done.stderr or ''}")
    return match.group(0) if match else None


def sdk_version() -> str | None:
    try:
        return metadata.version("google-antigravity")
    except metadata.PackageNotFoundError:
        return None
    except Exception:  # noqa: BLE001 - a damaged distribution must not break a read-only check
        return None


def _config_keys(gemini_dir: Path) -> list[str]:
    """Top-level key names of Antigravity's config.json (names only, never values)."""
    try:
        data = json.loads((gemini_dir / "config" / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return sorted(k for k in data if isinstance(k, str)) if isinstance(data, dict) else []


def probe(gemini_dir: Path | None = None, *, agy: Sequence[str] | str | None = "auto",
          gemini_cli: Sequence[str] | str | None = "auto", timeout: float = VERSION_TIMEOUT) -> Snapshot:
    """Look at the installation. ``agy`` / ``gemini_cli``: ``"auto"`` to find them, a command prefix to use, or None to
    skip running anything (offline fixtures)."""
    base = gemini_dir if gemini_dir is not None else paths.gemini_dir()
    snap = Snapshot()
    root = find_skills_root(base)
    if root is None:
        snap.notes.append("no Antigravity skills folder found under " + str(base))
    else:
        snap.skills_root = str(root)
        snap.doc_hashes, snap.markers, extra = read_docs(root)
        snap.notes += extra
        with contextlib.suppress(OSError):
            snap.builtin_skills = sorted(p.name for p in root.iterdir() if p.is_dir())
        if not snap.doc_hashes:
            snap.notes.append("the Antigravity docs AG-Flow checks were not found")
    snap.config_keys = _config_keys(base)

    agy_cmd = find_agy() if agy == "auto" else (list(agy) if agy and not isinstance(agy, str) else None)
    if agy_cmd:
        snap.agy_path = agy_cmd[0]
        snap.agy_version = tool_version(agy_cmd, timeout)
        if snap.agy_version is None:
            snap.notes.append("`agy --version` gave no version")
    gem_cmd = ([shutil.which("gemini")] if gemini_cli == "auto" and shutil.which("gemini")
               else (list(gemini_cli) if gemini_cli and not isinstance(gemini_cli, str) else None))
    if gem_cmd and gem_cmd[0]:
        snap.gemini_cli_version = tool_version(gem_cmd, timeout)
    snap.sdk_version = sdk_version()
    return snap
