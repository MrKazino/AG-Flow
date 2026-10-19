"""Run persistence: <workspace>/.agents/agflow/runs/<run_id>/{state.json, ledger.jsonl, report.*}.

``<runs>/.index.json`` is a small status index (is a run ``running``? when was it created?) so the hot paths --
the PostToolUse hook runs on *every* agent tool call -- do not have to read and validate every ``state.json``.
It is a cache, never the source of truth: ``state.json`` always wins, readers reconcile the index against the
directory listing (indexing runs they have not seen, dropping ones that vanished), the runs they act on are
re-validated from their own state file, and a damaged index is set aside as ``.index.json.corrupt`` and rebuilt.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from filelock import FileLock

from agflow import paths
from agflow.fsutil import atomic_write_text, read_json, write_json
from agflow.graph import engine
from agflow.graph.engine import RunState
from agflow.graph.schema import Graph
from agflow.redact import redact_mapping

RUN_INDEX_MAX = 500
INDEX_FILE = paths.RUN_INDEX_FILE
INDEX_VERSION = paths.RUN_INDEX_VERSION

# Every event name written to a run's ledger (``ledger.jsonl``), with what it records. The writers are the engine (state
# changes), the runner and the MCP server (dispatch and reports), the dashboard and CLI (abort) and the hooks. A new
# event is registered here first; tests/test_sync_audits.py fails for a name that is used but not listed (and for one
# that is listed but no longer used), so the ledger's vocabulary cannot drift unnoticed.
LEDGER_EVENTS: dict[str, str] = {
    "run_started": "a run was created (graph id and the redacted inputs)",
    "dispatched": "a node was handed to an agent (node, agent, attempt)",
    "reported": "an agent's result for a node (status, summary, artifacts)",
    "succeeded": "a node finished successfully",
    "failed": "a node failed for good (no retries left, or a `when` that could not be evaluated)",
    "retry": "a failed node goes back to ready for another attempt",
    "blocked": "a node cannot run because an upstream node failed",
    "skipped": "a node's `when` condition was false",
    "gate_passed": "a gate's dependencies were satisfied",
    "gate_failed": "a gate failed and cannot rework",
    "rework": "a failed gate sent its upstream node (and everything after it) back with feedback",
    "waiting_on_human": "a human node is waiting for a decision (runner)",
    "human_decision": "a human approved or rejected a node",
    "resumed": "running nodes were released after a crash or reload",
    "interrupted": "the headless runner was interrupted and released its running nodes",
    "report_conflict": "a report for a node was refused because someone else had already settled it",
    "evidence_rejected": "a `succeeded` report was refused because its evidence check failed (the node is retried "
                         "with feedback naming what is missing)",
    "lessons_recorded": "a finished run was added to the workflow's memory (how many lessons it taught)",
    "evidence_warning": "a report was accepted with notes from the evidence check (stale output, a mentioned file "
                        "that does not exist)",
    "runner_stopped": "the headless runner finished (final run status)",
    "aborted": "the run was aborted",
    "tool": "an agent tool call, recorded by the PostToolUse hook",
    "guard_unavailable": "a hook payload the safety guard could not read (once per reason per run)",
    "run_index_unwritable": "the global run index could not be updated, so another workspace's MCP server may not find "
                           "this run by its id (the run itself is intact)",
}


def _entry(state: RunState) -> dict[str, Any]:
    return {"graph_id": state.graph_id, "status": state.status, "created_at": state.created_at,
            "updated_at": state.updated_at}


class RunNotFound(KeyError):
    pass


class RunStore:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.root = paths.runs_dir(self.workspace)

    def run_dir(self, run_id: str) -> Path:
        if "/" in run_id or "\\" in run_id or run_id.startswith("."):
            raise RunNotFound(run_id)
        return self.root / run_id

    def _state_file(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "state.json"

    def _lock(self, run_id: str) -> FileLock:
        d = self.run_dir(run_id)
        d.mkdir(parents=True, exist_ok=True)
        return FileLock(str(d / ".lock"), timeout=30)

    # -- lifecycle ---------------------------------------------------------------------------
    def create(self, graph: Graph, inputs: dict[str, Any], *, graph_path: str | None = None,
               now: float | None = None) -> RunState:
        now = now or time.time()
        state = engine.new_run(graph, inputs, str(self.workspace), now=now, graph_path=graph_path)
        with self._lock(state.run_id):
            self._write(state)
        self.ledger(state.run_id, {"event": "run_started", "graph": graph.id, "inputs": redact_mapping(inputs)})
        problem = register_run(state.run_id, self.workspace)
        if problem:  # an optimisation, not the truth -- but a missing entry must not be invisible (N1)
            self.ledger(state.run_id, {"event": "run_index_unwritable", "reason": problem})
        return state

    def exists(self, run_id: str) -> bool:
        try:
            return self._state_file(run_id).is_file()
        except RunNotFound:
            return False

    def load(self, run_id: str) -> RunState:
        f = self._state_file(run_id)
        if not f.is_file():
            raise RunNotFound(run_id)
        return RunState.model_validate(read_json(f))

    def _write(self, state: RunState) -> None:
        # the durable state first: if the index update then fails, readers reconcile it (state.json always wins)
        write_json(self._state_file(state.run_id), state.model_dump(mode="json"))
        self._update_index({state.run_id: _entry(state)}, ())

    # -- status index -------------------------------------------------------------------------
    def _index_path(self) -> Path:
        return self.root / INDEX_FILE

    def _index_lock(self) -> FileLock:
        self.root.mkdir(parents=True, exist_ok=True)
        return FileLock(str(self.root / ".index.lock"), timeout=30)

    def _read_index(self) -> dict[str, dict[str, Any]]:
        """The cached entries. A damaged index is moved aside (evidence for `doctor`) and treated as empty."""
        f = self._index_path()
        try:
            data = read_json(f, None)
        except (OSError, ValueError):
            data = False  # unreadable or not JSON
        if data is None:
            return {}
        runs = data.get("runs") if isinstance(data, dict) else None
        if not isinstance(runs, dict) or not all(isinstance(e, dict) for e in runs.values()):
            os.replace(f, f.with_name(INDEX_FILE + ".corrupt"))
            return {}
        return runs if data.get("version") == INDEX_VERSION else {}

    def _update_index(self, upserts: dict[str, dict[str, Any]], removals: Any,
                      discovered: dict[str, dict[str, Any]] | None = None) -> None:
        """Apply changes under the index lock. ``upserts`` overwrite; ``discovered`` (what a reader found on disk)
        only fills gaps, so a reader can never replace the fresher entry a concurrent writer just stored."""
        with self._index_lock():
            entries = self._read_index()  # re-read under the lock so concurrent writers never lose each other
            for run_id in removals:
                entries.pop(run_id, None)
            for run_id, found in (discovered or {}).items():
                entries.setdefault(run_id, found)
            entries.update(upserts)
            write_json(self._index_path(), {"version": INDEX_VERSION, "runs": entries})

    def _listed_run_ids(self) -> set[str]:
        """Run directories by name only (no per-run file access): this is what keeps the lookup flat."""
        with os.scandir(self.root) as it:
            return {e.name for e in it if not e.name.startswith(".") and e.is_dir()}

    def _entry_from_disk(self, run_id: str) -> dict[str, Any] | None:
        """Index entry for one run, parsed from its state file. ``None``: no state file yet."""
        f = self._state_file(run_id)
        try:
            st = f.stat()
        except FileNotFoundError:
            return None
        try:
            return _entry(RunState.model_validate(read_json(f)))
        except (OSError, ValueError):  # unreadable, not JSON, or not a valid state: listings skip it, doctor says so
            return {"corrupt": True, "sig": [st.st_mtime_ns, st.st_size]}

    def _reconcile(self) -> dict[str, dict[str, Any]]:
        """Index entries for exactly the run directories that exist, parsing only what is new or changed."""
        if not self.root.is_dir():
            return {}
        entries = self._read_index()
        listed = self._listed_run_ids()
        discovered: dict[str, dict[str, Any]] = {}
        rechecked: dict[str, dict[str, Any]] = {}
        removals = [run_id for run_id in entries if run_id not in listed]
        for run_id in sorted(listed - entries.keys()):
            found = self._entry_from_disk(run_id)
            if found is not None:
                discovered[run_id] = found
        for run_id, known in entries.items():
            if known.get("corrupt") and run_id in listed:  # re-check a corrupt run only if its file changed
                try:
                    st = self._state_file(run_id).stat()
                except FileNotFoundError:
                    continue
                if known.get("sig") != [st.st_mtime_ns, st.st_size]:
                    found = self._entry_from_disk(run_id)
                    if found is not None:
                        rechecked[run_id] = found
        if discovered or rechecked or removals:
            self._update_index(rechecked, removals, discovered)
            for run_id in removals:
                entries.pop(run_id)
            entries.update(discovered)
            entries.update(rechecked)
        return entries

    def health(self) -> dict[str, Any]:
        """What `doctor` reports: runs whose state cannot be read, and whether the index had to be rebuilt."""
        entries = self._reconcile()
        return {"corrupt_runs": sorted(r for r, e in entries.items() if e.get("corrupt")),
                "index_damaged": self._index_path().with_name(INDEX_FILE + ".corrupt").exists()}

    @contextmanager
    def transaction(self, run_id: str) -> Iterator[RunState]:
        """Load, mutate and save a run under an inter-process lock."""
        with self._lock(run_id):
            state = self.load(run_id)
            yield state
            self._write(state)

    def ledger(self, run_id: str, event: dict[str, Any]) -> None:
        line = json.dumps({"ts": time.time(), **event}, ensure_ascii=False, default=str)
        f = self.run_dir(run_id) / "ledger.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def read_ledger(self, run_id: str, limit: int = 500) -> list[dict[str, Any]]:
        f = self.run_dir(run_id) / "ledger.jsonl"
        if not f.is_file():
            return []
        lines = f.read_text(encoding="utf-8").splitlines()[-limit:]
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def write_report(self, state: RunState) -> tuple[Path, Path]:
        graph = state.graph_model()
        view = engine.status_view(graph, state, verbose=True)
        view["inputs"] = redact_mapping(view["inputs"])  # reports are shared; state.json keeps the real values
        d = self.run_dir(state.run_id)
        jpath, mpath = d / "report.json", d / "report.md"
        write_json(jpath, view)
        atomic_write_text(mpath, render_report_md(graph, state))
        return jpath, mpath

    # -- queries -----------------------------------------------------------------------------
    def list(self, limit: int | None = None) -> list[RunState]:
        """Runs, newest first. Unreadable runs are skipped (``health()`` reports them).

        Without ``limit`` every run is read and validated. With ``limit`` the index picks the newest candidates
        and only those are parsed, so a page of 50 costs 50 reads however many runs the workspace holds."""
        if not self.root.is_dir():
            return []
        if limit is None:
            runs = []
            for d in self.root.iterdir():
                f = d / "state.json"
                if f.is_file():
                    try:
                        runs.append(RunState.model_validate(read_json(f)))
                    except (OSError, ValueError):
                        continue
            return sorted(runs, key=lambda r: r.created_at, reverse=True)
        entries = self._reconcile()
        newest_first = sorted((r for r, e in entries.items() if not e.get("corrupt")),
                              key=lambda r: entries[r].get("created_at", 0.0), reverse=True)
        page: list[RunState] = []
        for run_id in newest_first:
            if len(page) >= limit:
                break
            try:
                page.append(self.load(run_id))
            except (OSError, ValueError, RunNotFound):
                continue  # went bad or vanished since it was indexed
        return sorted(page, key=lambda r: r.created_at, reverse=True)

    def active(self) -> list[RunState]:
        """Runs whose status is ``running``. Cost depends on how many are running, not on how many exist."""
        entries = self._reconcile()
        running: list[RunState] = []
        corrections: dict[str, dict[str, Any]] = {}
        removals: list[str] = []
        for run_id, known in entries.items():
            if known.get("corrupt") or known.get("status") != "running":
                continue
            try:
                state = self.load(run_id)  # the state file is the truth, whatever the index says
            except RunNotFound:
                removals.append(run_id)
                continue
            except (OSError, ValueError):
                found = self._entry_from_disk(run_id)
                if found is not None:
                    corrections[run_id] = found
                continue
            if state.status == "running":
                running.append(state)
            else:
                corrections[run_id] = _entry(state)  # finished since it was indexed (e.g. a crash between writes)
        if corrections or removals:
            self._update_index(corrections, removals)
        return sorted(running, key=lambda r: r.created_at, reverse=True)


def render_report_md(graph: Graph, state: RunState) -> str:
    icons = {"succeeded": "✅", "failed": "❌", "skipped": "⏭️", "blocked": "⛔", "running": "⏳", "pending": "•"}
    lines = [f"# Run `{state.run_id}` — {state.status.upper()}", "",
             f"- Graph: `{graph.id}` {('— ' + graph.title) if graph.title else ''}",
             f"- Workspace: `{state.workspace}`",
             f"- Started: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(state.created_at))}",
             f"- Updated: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(state.updated_at))}"]
    if state.inputs:
        lines += ["", "## Inputs", ""] + [f"- **{k}**: {v}" for k, v in redact_mapping(state.inputs).items()]
    if state.loops:
        lines += ["", "## Rework loops", ""] + [f"- {g}: {n}" for g, n in state.loops.items()]
    lines += ["", "## Nodes", "", "| Node | Agent | Status | Attempts | Summary |", "|---|---|---|---|---|"]
    for n in graph.nodes:
        ns = state.nodes[n.id]
        summary = (ns.summary or ns.error or "").replace("\n", " ").replace("|", "\\|")[:300]
        lines.append(f"| {n.id} | {n.agent or n.type} | {icons.get(ns.status.value, '')} {ns.status.value} "
                     f"| {ns.attempts} | {summary} |")
    arts = sorted({a for ns in state.nodes.values() for a in ns.artifacts})
    if arts:
        lines += ["", "## Artifacts", ""] + [f"- `{a}`" for a in arts]
    follow = [f"- ({nid}) {f}" for nid, ns in state.nodes.items() for f in ns.followups]
    if follow:
        lines += ["", "## Follow-ups", ""] + follow
    return "\n".join(lines) + "\n"


# --- global run index (run_id -> workspace), used by the MCP server to resolve runs ------------


def _index_file() -> Path:
    return paths.global_state_dir() / "runs-index.json"


def register_run(run_id: str, workspace: Path) -> str | None:
    """Record ``run_id -> workspace`` in the global index. Returns ``None``, or why it could not (the exception type
    and message): the index is only a shortcut for finding a run from another workspace, so a failure never fails
    the run -- but the caller records it, so it is not silent."""
    f = _index_file()
    try:
        with FileLock(str(f) + ".lock", timeout=10):
            data = read_json(f, {}) or {}
            data[run_id] = str(workspace)
            if len(data) > RUN_INDEX_MAX:
                data = dict(list(data.items())[-RUN_INDEX_MAX:])
            write_json(f, data)
    except (OSError, ValueError) as exc:  # filelock's Timeout is an OSError; a damaged index is a ValueError
        return f"{type(exc).__name__}: {exc}"[:200]
    return None


def known_workspaces(limit: int = 20) -> list[Path]:
    """Workspaces that have had runs, newest first (from the global run index); folders that are gone are left out."""
    data = read_json(_index_file(), {}) or {}
    seen: list[Path] = []
    for ws in reversed(list(data.values())):
        path = Path(ws)
        if path not in seen and path.is_dir():
            seen.append(path)
    return seen[:limit]


def lookup_workspace(run_id: str) -> Path | None:
    data = read_json(_index_file(), {}) or {}
    ws = data.get(run_id)
    return Path(ws) if ws else None
