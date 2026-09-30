"""Run persistence: <workspace>/.agents/agflow/runs/<run_id>/{state.json, ledger.jsonl, report.*}."""

from __future__ import annotations

import json
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

RUN_INDEX_MAX = 500


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
        self.ledger(state.run_id, {"event": "run_started", "graph": graph.id, "inputs": inputs})
        register_run(state.run_id, self.workspace)
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
        write_json(self._state_file(state.run_id), state.model_dump(mode="json"))

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
        d = self.run_dir(state.run_id)
        jpath, mpath = d / "report.json", d / "report.md"
        write_json(jpath, view)
        atomic_write_text(mpath, render_report_md(graph, state))
        return jpath, mpath

    # -- queries -----------------------------------------------------------------------------
    def list(self) -> list[RunState]:
        if not self.root.is_dir():
            return []
        runs = []
        for d in self.root.iterdir():
            f = d / "state.json"
            if f.is_file():
                try:
                    runs.append(RunState.model_validate(read_json(f)))
                except Exception:
                    continue
        return sorted(runs, key=lambda r: r.created_at, reverse=True)

    def active(self) -> list[RunState]:
        return [r for r in self.list() if r.status == "running"]


def render_report_md(graph: Graph, state: RunState) -> str:
    icons = {"succeeded": "✅", "failed": "❌", "skipped": "⏭️", "blocked": "⛔", "running": "⏳", "pending": "•"}
    lines = [f"# Run `{state.run_id}` — {state.status.upper()}", "",
             f"- Graph: `{graph.id}` {('— ' + graph.title) if graph.title else ''}",
             f"- Workspace: `{state.workspace}`",
             f"- Started: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(state.created_at))}",
             f"- Updated: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(state.updated_at))}"]
    if state.inputs:
        lines += ["", "## Inputs", ""] + [f"- **{k}**: {v}" for k, v in state.inputs.items()]
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


def register_run(run_id: str, workspace: Path) -> None:
    f = _index_file()
    try:
        with FileLock(str(f) + ".lock", timeout=10):
            data = read_json(f, {}) or {}
            data[run_id] = str(workspace)
            if len(data) > RUN_INDEX_MAX:
                data = dict(list(data.items())[-RUN_INDEX_MAX:])
            write_json(f, data)
    except OSError:
        pass  # index is an optimisation only


def lookup_workspace(run_id: str) -> Path | None:
    data = read_json(_index_file(), {}) or {}
    ws = data.get(run_id)
    return Path(ws) if ws else None
