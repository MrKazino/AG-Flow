"""Evidence for what an agent claims it did.

A model can report "succeeded" with a confident summary and a list of files it never wrote. This module checks the
claim against the workspace before the engine accepts it. **It only looks**: it never runs a command and never takes
instructions from the graph beyond the declarative ``outputs`` and ``checks`` a node lists.

What is checked for a node that reports ``succeeded`` (agent nodes only):

* the summary says something checkable (not empty, not "done");
* every declared ``outputs`` path exists inside the workspace and is not empty (a trailing ``/`` means a folder);
* every reported artifact exists (``deleted: <path>`` records a deletion; URLs and paths outside the workspace are not
  checked);
* every ``checks`` entry holds (file exists, minimum size, must contain / must not contain a literal string);
* notes that are never blocking: an output not modified during this attempt, and files the summary mentions in
  backticks that do not exist.

The node's ``evidence`` mode decides what a failed check means: ``off`` skips everything, ``advisory`` (the default)
records the findings as warnings and accepts the report, ``required`` turns a failed check into a failed attempt whose
feedback names exactly what is missing, so the retry knows what to fix.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agflow.graph import engine
from agflow.graph.schema import Check, Graph

MIN_SUMMARY = 20
MAX_READ = 1_000_000
MAX_ARTIFACTS = 50
MAX_MENTIONS = 5
MTIME_SLACK = 2.0  # seconds: coarse filesystem timestamps and small clock differences are not staleness
VAGUE = frozenset({"done", "ok", "okay", "complete", "completed", "finished", "success", "succeeded", "fixed",
                   "all good", "all done", "looks good", "implemented", "n/a", "none"})
_MENTION = re.compile(r"`([A-Za-z0-9_][A-Za-z0-9_./\\-]*\.[A-Za-z0-9]{1,6})`")
_VERSION_LIKE = re.compile(r"^\d+(?:\.\d+)+$")


@dataclass
class Evidence:
    mode: str = "off"
    problems: list[str] = field(default_factory=list)   # blocking (only under ``required``)
    warnings: list[str] = field(default_factory=list)

    @property
    def rejected(self) -> bool:
        return bool(self.problems)

    def to_json(self) -> dict[str, Any]:
        return {"mode": self.mode, "rejected": self.rejected, "problems": self.problems, "warnings": self.warnings}


def within(root: Path, text: str) -> Path | None:
    """The real path of ``text`` (relative to ``root`` or absolute) if it lies inside ``root``, else None."""
    if not text or "\x00" in text:
        return None
    try:
        candidate = Path(text)
        real = Path(os.path.realpath(candidate if candidate.is_absolute() else root / candidate))
        base = Path(os.path.realpath(root))
        if os.path.commonpath([os.path.normcase(real), os.path.normcase(base)]) != os.path.normcase(base):
            return None
    except (ValueError, OSError):
        return None
    return real


def _emptiness(real: Path) -> str:
    """Why ``real`` does not count as present ('' when it does)."""
    try:
        if not real.exists():
            return "does not exist"
        if real.is_dir():
            return "" if any(True for _ in real.iterdir()) else "is an empty folder"
        return "" if real.stat().st_size > 0 else "is empty"
    except OSError:
        return "cannot be read"


def _read(real: Path) -> str | None:
    try:
        with real.open("rb") as fh:
            return fh.read(MAX_READ).decode("utf-8", errors="replace")
    except OSError:
        return None


def _show(text: str, limit: int = 60) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def check(graph: Graph, state: engine.RunState, node_id: str, *, status: str, summary: str = "",
          artifacts: list[str] | None = None) -> Evidence:
    """Verify a report before the engine accepts it. Never raises; an unreadable path is a finding, not an error."""
    try:
        node = graph.node(node_id)
    except KeyError:
        return Evidence()
    mode = graph.evidence_for(node)
    if mode == "off" or node.type != "agent" or status != "succeeded":
        return Evidence(mode)
    root = Path(state.workspace)
    ctx = engine.node_context(graph, state, node_id)
    started = state.nodes[node_id].started_at
    problems: list[str] = []
    warnings: list[str] = []

    text = (summary or "").strip()
    if len(text) < MIN_SUMMARY or text.lower().rstrip(".! ") in VAGUE:
        problems.append("the summary says nothing checkable: name what was done and the evidence (commands run, files "
                        "changed, results)")

    checked: set[str] = set()
    for raw in node.outputs:
        out = engine.render_text(raw, ctx).strip()
        folder = out.endswith(("/", "\\"))
        checked.add(out.rstrip("/\\"))
        real = within(root, out)
        if real is None:
            problems.append(f"expected output {out!r} is outside the workspace")
            continue
        if any(c in out for c in "*?["):
            hits = [p for p in root.glob(out) if _emptiness(p) == ""] if not Path(out).is_absolute() else []
            if not hits:
                problems.append(f"expected output {out!r} matches no non-empty file")
            continue
        why = _emptiness(real)
        if why:
            problems.append(f"expected output {out!r} {why}")
            continue
        if folder and not real.is_dir():
            problems.append(f"expected output {out!r} is a file, but a folder was expected")
        elif started and real.is_file() and real.stat().st_mtime < started - MTIME_SLACK:
            warnings.append(f"{out} was not modified during this attempt: is it left over from an earlier run?")

    for raw in (artifacts or [])[:MAX_ARTIFACTS]:
        art = str(raw).strip()
        if not art or art.lower().startswith("deleted:") or "://" in art:
            continue
        checked.add(art.rstrip("/\\"))
        real = within(root, art)
        if real is None:
            warnings.append(f"artifact {art!r} is outside the workspace: not checked")
        elif not real.exists():
            problems.append(f"reported artifact {art!r} does not exist")

    for item in node.checks:
        problems += _run_check(item, root, ctx)

    mentioned = []
    for token in dict.fromkeys(_MENTION.findall(text)):
        if token.rstrip("/\\") in checked or "://" in token or _VERSION_LIKE.match(token):
            continue
        target = within(root, token)
        if target is not None and not target.exists():
            mentioned.append(token)
    if mentioned:
        listed = ", ".join(mentioned[:MAX_MENTIONS]) + (" ..." if len(mentioned) > MAX_MENTIONS else "")
        warnings.append(f"the summary mentions files that do not exist in the workspace: {listed}")

    if mode == "advisory":
        return Evidence(mode, [], problems + warnings)
    return Evidence(mode, problems, warnings)


def _run_check(item: Check, root: Path, ctx: dict[str, Any]) -> list[str]:
    name = engine.render_text(item.file, ctx).strip()
    real = within(root, name)
    if real is None:
        return [f"check on {name!r}: the path is outside the workspace"]
    why = _emptiness(real)
    if why:
        return [f"check on {name!r}: the file {why}"]
    found: list[str] = []
    if item.min_bytes is not None and real.is_file() and real.stat().st_size < item.min_bytes:
        found.append(f"{name} is smaller than {item.min_bytes} bytes")
    if (item.contains or item.not_contains) and real.is_file():
        body = _read(real)
        if body is None:
            return found + [f"check on {name!r}: the file cannot be read"]
        found += [f"{name} must contain {_show(want)!r}" for want in (engine.render_text(s, ctx) for s in item.contains)
                  if want not in body]
        found += [f"{name} must not contain {_show(bad)!r}" for bad in (engine.render_text(s, ctx)
                                                                         for s in item.not_contains) if bad in body]
    return found
