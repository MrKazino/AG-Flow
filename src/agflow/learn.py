"""What earlier runs of a workflow taught, kept per workflow and never acted on without the user.

When a run finishes, :func:`record_run` writes two small files under ``<workspace>/.agents/agflow/workflows/<graph>/``:

* ``runs.jsonl``    - one line per finished run (status, how many attempts each node needed);
* ``lessons.jsonl`` - evidence-backed observations: a step the evidence check sent back and why, a reviewer's rejection,
  a gate that forced rework, a timeout, a backend error.

Lessons are extracted from the run's own record (its ledger and node history) by fixed rules; nothing is guessed and no
model is called. Text is redacted (:mod:`agflow.redact`) and bounded, the files are capped, and recording never raises:
a lesson that cannot be written must not break the run that produced it.

Two things read the lessons: :func:`LessonIndex.for_node` puts the few that matter into the task of the step they
concern, and :mod:`agflow.improve` turns recurring ones into *proposed* workflow changes that the user approves one
workflow at a time.
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from agflow import paths
from agflow.fsutil import atomic_write_text
from agflow.graph.engine import RunState
from agflow.graph.schema import ID_RE
from agflow.redact import redact_text

RUNS_FILE = "runs.jsonl"
LESSONS_FILE = "lessons.jsonl"
MAX_RUN_LINES = 200
MAX_LESSON_LINES = 500
MAX_LESSONS_PER_RUN = 20
DETAIL_MAX = 240
WINDOW_DAYS = 90.0
HALF_LIFE_DAYS = 30.0
CARD_LINES = 3
CARD_CHARS = 700
DAY = 86400.0

# kind -> how many distinct runs must show it before it is worth telling an agent about (or proposing a change for)
MIN_RUNS_FOR_CARD = {"evidence_rejected": 1, "review_rejected": 1, "rework": 1, "timeout": 2, "backend_error": 2,
                     "failed_report": 2, "evidence_warning": 2}
KINDS = ("evidence_rejected", "evidence_warning", "review_rejected", "rework", "timeout", "backend_error",
         "failed_report", "human_rejected", "run_failed")
_NUMBERS = re.compile(r"\d+")
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Lesson:
    kind: str
    node: str
    detail: str
    run: str
    at: float

    @property
    def key(self) -> tuple[str, str, str]:
        """What makes two observations "the same lesson": same kind, same step, same wording up to the numbers."""
        return self.kind, self.node, _NUMBERS.sub("#", self.detail.lower())[:80]

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "node": self.node, "detail": self.detail, "run": self.run, "at": self.at}

    @classmethod
    def from_json(cls, data: Any) -> Lesson | None:
        if not isinstance(data, dict):
            return None
        kind, node, detail, run, at = (data.get(k) for k in ("kind", "node", "detail", "run", "at"))
        if kind not in KINDS or not all(isinstance(v, str) for v in (node, detail, run)):
            return None
        return cls(kind, node, detail, run, float(at) if isinstance(at, (int, float)) else 0.0)  # type: ignore[arg-type]


@dataclass(frozen=True)
class Pattern:
    """The same lesson seen in one or more runs."""

    kind: str
    node: str
    detail: str
    runs: int
    last_at: float
    score: float

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "node": self.node, "detail": self.detail, "runs": self.runs,
                "last_at": self.last_at, "score": round(self.score, 3)}


def clean(text: Any) -> str:
    return _SPACE.sub(" ", redact_text(str(text or ""))).strip()[:DETAIL_MAX]


# --- extraction (pure) -----------------------------------------------------------------------------------------------


def extract(state: RunState, ledger: list[dict[str, Any]], *, at: float) -> list[Lesson]:
    """The lessons in one run, from its ledger and node history. Deterministic; at most ``MAX_LESSONS_PER_RUN``."""
    found: list[Lesson] = []

    def add(kind: str, node: str, detail: Any) -> None:
        text = clean(detail)
        if text or kind in ("rework",):
            found.append(Lesson(kind, node, text, state.run_id, at))

    for event in ledger:
        name, node = event.get("event"), str(event.get("node") or "")
        if name == "evidence_rejected":
            add("evidence_rejected", node, "; ".join(map(str, event.get("problems") or [])))
        elif name == "evidence_warning":
            add("evidence_warning", node, "; ".join(map(str, event.get("notes") or [])))
        elif name == "reported" and event.get("status") == "rejected":
            add("review_rejected", node, event.get("summary"))
        elif name == "reported" and event.get("status") == "failed":
            summary = str(event.get("summary") or "")
            add("backend_error" if summary.startswith("backend error") else "failed_report", node, summary)
        elif name == "rework":
            add("rework", str(event.get("goto") or ""), f"gate '{event.get('gate')}' sent it back "
                                                         f"(loop {event.get('loop')})")
        elif name == "human_decision" and event.get("approved") is False:
            add("human_rejected", node, event.get("note") or "rejected without a note")
    for node_id, ns in state.nodes.items():
        errors = [str(h.get("error") or "") for h in ns.history if h.get("status") == "failed"] + [ns.error]
        if any("lease expired" in e for e in errors):
            add("timeout", node_id, next(e for e in errors if "lease expired" in e))
    if state.status == "failed":
        failed = [nid for nid, ns in state.nodes.items() if ns.status.value == "failed"]
        add("run_failed", "", "failed nodes: " + ", ".join(failed) if failed else "the run failed")

    unique: dict[tuple[str, str, str], Lesson] = {}
    for lesson in found:
        unique.setdefault((lesson.kind, lesson.node, lesson.detail), lesson)
    return list(unique.values())[:MAX_LESSONS_PER_RUN]


def run_row(state: RunState, *, at: float) -> dict[str, Any]:
    return {"run": state.run_id, "status": state.status, "created_at": state.created_at, "at": at,
            "nodes": {nid: {"status": ns.status.value, "attempts": ns.attempts} for nid, ns in state.nodes.items()}}


# --- storage ---------------------------------------------------------------------------------------------------------


def workflow_dir(workspace: Path, graph_id: str) -> Path | None:
    return paths.workflow_state_dir(workspace, graph_id) if ID_RE.match(graph_id or "") else None


def _read_lines(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    rows = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _write_lines(path: Path, rows: list[dict[str, Any]], cap: int) -> None:
    atomic_write_text(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows[-cap:]), durable=False)


def load_runs(workspace: Path, graph_id: str) -> list[dict[str, Any]]:
    folder = workflow_dir(workspace, graph_id)
    return _read_lines(folder / RUNS_FILE) if folder else []


def load_lessons(workspace: Path, graph_id: str) -> list[Lesson]:
    folder = workflow_dir(workspace, graph_id)
    rows = _read_lines(folder / LESSONS_FILE) if folder else []
    return [lesson for lesson in map(Lesson.from_json, rows) if lesson is not None]


def record_run(workspace: Path, state: RunState, ledger: list[dict[str, Any]], *, now: float | None = None) -> int:
    """Remember a finished run. Returns how many lessons were written (0 for a run already recorded, one that is still
    running, one that was aborted, or any failure to write: recording must never disturb the run)."""
    moment = time.time() if now is None else now
    folder = workflow_dir(workspace, state.graph_id)
    if folder is None or state.status == "running":
        return 0
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with FileLock(str(folder / ".lock"), timeout=5):
            runs = _read_lines(folder / RUNS_FILE)
            if any(r.get("run") == state.run_id for r in runs):
                return 0
            _write_lines(folder / RUNS_FILE, [*runs, run_row(state, at=moment)], MAX_RUN_LINES)
            if state.status == "aborted":
                return 0
            fresh = extract(state, ledger, at=moment)
            if fresh:
                rows = _read_lines(folder / LESSONS_FILE)
                _write_lines(folder / LESSONS_FILE, [*rows, *(lesson.to_json() for lesson in fresh)], MAX_LESSON_LINES)
            return len(fresh)
    except (OSError, ValueError, Timeout):
        return 0


# --- reading ---------------------------------------------------------------------------------------------------------


def patterns(lessons: list[Lesson], *, now: float | None = None, node: str | None = None) -> list[Pattern]:
    """Lessons grouped into patterns (inside the window), strongest first: distinct runs, weighted by recency."""
    moment = time.time() if now is None else now
    groups: dict[tuple[str, str, str], list[Lesson]] = defaultdict(list)
    for lesson in lessons:
        if (node is None or lesson.node == node) and moment - lesson.at <= WINDOW_DAYS * DAY:
            groups[lesson.key].append(lesson)
    out = []
    for members in groups.values():
        newest = max(members, key=lambda item: item.at)
        runs = len({item.run for item in members})
        per_run = {item.run: 0.5 ** (max(0.0, moment - item.at) / DAY / HALF_LIFE_DAYS) for item in members}
        weight = sum(per_run.values())
        out.append(Pattern(newest.kind, newest.node, newest.detail, runs, newest.at, weight))
    return sorted(out, key=lambda p: (-p.score, -p.last_at, p.kind, p.node))


_CARD_TEXT = {
    "evidence_rejected": "An earlier attempt at this step was sent back by the evidence check ({n}x): {detail}",
    "review_rejected": "A reviewer rejected earlier work at this step ({n}x): {detail}",
    "rework": "A quality gate sent this step back before ({n}x): {detail}",
    "timeout": "This step ran out of time in {n} earlier runs: work in small, verified pieces and report early.",
    "backend_error": "This step hit a backend error in {n} earlier runs: {detail}",
    "failed_report": "This step failed in {n} earlier runs: {detail}",
    "evidence_warning": "The evidence check noted this in {n} earlier runs: {detail}",
}


class LessonIndex:
    """The lessons of one workflow, read once, so a batch of cards costs one read."""

    def __init__(self, workspace: Path, graph_id: str, *, now: float | None = None):
        self.now = time.time() if now is None else now
        self.lessons = load_lessons(workspace, graph_id)

    def for_node(self, node_id: str, *, limit: int = CARD_LINES) -> list[str]:
        """Short sentences for the task of ``node_id``, drawn from patterns seen often enough to matter."""
        lines: list[str] = []
        used = 0
        for pat in patterns(self.lessons, now=self.now, node=node_id):
            if pat.kind not in _CARD_TEXT or pat.runs < MIN_RUNS_FOR_CARD.get(pat.kind, 99):
                continue
            line = _CARD_TEXT[pat.kind].format(n=pat.runs, detail=pat.detail)
            if used + len(line) > CARD_CHARS or len(lines) >= limit:
                break
            lines.append(line)
            used += len(line)
        return lines


def lessons_for_card(workspace: Path, graph_id: str, node_ids: list[str]) -> dict[str, list[str]]:
    """``{node id: sentences}`` for the nodes being dispatched. Never raises."""
    try:
        index = LessonIndex(workspace, graph_id)
        return {nid: index.for_node(nid) for nid in node_ids}
    except Exception:  # noqa: BLE001 - a hint for the agent; the run does not depend on it
        return {}
