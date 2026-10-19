"""Which files of an installed plugin are still exactly what agflow installed, and what an update does with the rest.

``agflow install`` replaces the whole plugin folder. That is right for a repair ("restore the original files") and wrong
for an update, where it would silently throw away a skill you tuned or a graph you extended. So every install records a
fingerprint of what it installed (``install.files.json`` next to the install manifest), and an update:

1. copies the live folder aside (also the rollback copy of the plugin),
2. lets ``install`` write the new version,
3. puts your changes back on top (:func:`reapply_user_edits`), deciding per file:

   =============================================  =====================================================================
   you changed it, the new version did not        your file stays
   you changed it, the new version did too        **your file stays** (you win); the new version is saved under
                                                  ``<incoming>/<path>`` so you can merge it
   you added a file                               it stays
   you deleted a file the new version still has   it stays deleted; the new version is saved under ``<incoming>``
   you changed a file the new version dropped     it is not resurrected; your copy is saved under
                                                  ``<incoming>/removed-upstream/<path>``
   =============================================  =====================================================================

Files that ``install`` rewrites for this machine on every run (``hooks.json``, ``mcp_config.json``, each sidecar's
``sidecar.json``) are *managed*: they are regenerated, never compared, never preserved. Content is hashed with Windows
line endings folded to ``\\n`` so a checkout with ``autocrlf`` does not look like an edit.

Stdlib only.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

IGNORE_DIRS = ("__pycache__", ".git")
IGNORE_FILES = ("*.pyc", ".DS_Store", "Thumbs.db")
MANAGED_FILES = ("hooks.json", "mcp_config.json")


def is_managed(rel: str) -> bool:
    """Rewritten for this machine by every install: never compared, never preserved."""
    return rel in MANAGED_FILES or (rel.startswith("sidecars/") and rel.endswith("/sidecar.json"))


def _ignored(rel: str) -> bool:
    parts = rel.split("/")
    return any(p in IGNORE_DIRS for p in parts[:-1]) or any(fnmatch.fnmatch(parts[-1], pat) for pat in IGNORE_FILES)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def fingerprint(root: Path) -> dict[str, str]:
    """``{relative posix path: sha256}`` of every file under ``root``, managed files included."""
    out: dict[str, str] = {}
    if not root.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        for name in filenames:
            path = Path(dirpath, name)
            rel = path.relative_to(root).as_posix()
            if not _ignored(rel):
                out[rel] = _digest(path)
    return dict(sorted(out.items()))


def pristine(root: Path) -> dict[str, str]:
    """:func:`fingerprint` without the managed files: what a user can meaningfully edit."""
    return {rel: h for rel, h in fingerprint(root).items() if not is_managed(rel)}


@dataclass
class Changes:
    modified: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.modified or self.removed or self.added)


def changes(root: Path, recorded: dict[str, str]) -> Changes:
    """What differs between the folder as it is now and the fingerprint taken when it was installed."""
    now = pristine(root)
    return Changes(
        modified=sorted(rel for rel, h in now.items() if rel in recorded and recorded[rel] != h),
        removed=sorted(rel for rel in recorded if rel not in now),
        added=sorted(rel for rel in now if rel not in recorded))


# --- the record -----------------------------------------------------------------------------------------------------


def record_path(manifest_file: Path) -> Path:
    """``install.json`` -> ``install.files.json`` (same folder, so the scope decides where it lives)."""
    return manifest_file.with_name(manifest_file.stem + ".files.json")


def record(root: Path, record_file: Path, version: str) -> None:
    from agflow.fsutil import write_json

    write_json(record_file, {"version": version, "files": pristine(root)})


def load(record_file: Path) -> dict[str, str] | None:
    """The recorded fingerprint, or ``None`` when there is none or it is damaged (callers then fall back to the
    bundle of the running version)."""
    try:
        data = json.loads(record_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    files = data.get("files") if isinstance(data, dict) else None
    if isinstance(files, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in files.items()):
        return files
    return None


# --- putting the user's edits back ----------------------------------------------------------------------------------


@dataclass
class MergeReport:
    kept: list[str] = field(default_factory=list)            # your edit, upstream unchanged
    conflicts: list[str] = field(default_factory=list)       # both changed: yours kept, theirs in <incoming>
    restored_added: list[str] = field(default_factory=list)  # files you created
    kept_deleted: list[str] = field(default_factory=list)    # you deleted it; the new version is in <incoming>
    orphaned: list[str] = field(default_factory=list)        # you edited a file the new version dropped

    @property
    def clean(self) -> bool:
        return not (self.kept or self.conflicts or self.restored_added or self.kept_deleted or self.orphaned)

    def summary(self) -> list[str]:
        out = [f"kept your edit of {p}" for p in self.kept]
        out += [f"kept your version of {p} (the new version changed it too: saved for you to merge)"
                for p in self.conflicts]
        out += [f"kept your file {p}" for p in self.restored_added]
        out += [f"kept {p} deleted, as you left it (the new version is saved)" for p in self.kept_deleted]
        out += [f"{p} was removed upstream; your edited copy is saved, not restored" for p in self.orphaned]
        return out


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def reapply_user_edits(snapshot: Path, new_root: Path, old_recorded: dict[str, str], new_pristine: dict[str, str],
                       incoming: Path) -> MergeReport:
    """Put the changes found in ``snapshot`` (the folder as it was before the update) on top of ``new_root``."""
    rep = MergeReport()
    edits = changes(snapshot, old_recorded)
    for rel in edits.modified:
        mine, theirs = snapshot / rel, new_root / rel
        if rel not in new_pristine:
            _copy(mine, incoming / "removed-upstream" / rel)
            rep.orphaned.append(rel)
        elif new_pristine[rel] == old_recorded[rel]:
            _copy(mine, theirs)
            rep.kept.append(rel)
        else:
            _copy(theirs, incoming / rel)
            _copy(mine, theirs)
            rep.conflicts.append(rel)
    for rel in edits.added:
        theirs = new_root / rel
        if theirs.exists():
            _copy(theirs, incoming / rel)
        _copy(snapshot / rel, theirs)
        rep.restored_added.append(rel)
    for rel in edits.removed:
        theirs = new_root / rel
        if theirs.exists():
            _copy(theirs, incoming / rel)
            theirs.unlink()
            rep.kept_deleted.append(rel)
    return rep
