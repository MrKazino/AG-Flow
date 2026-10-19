"""Improving a workflow from what its runs taught: always a proposal, applied only when the user says so.

``agflow learn`` (this package's :mod:`agflow.learn`) records what went wrong in finished runs. This module turns a
*recurring* problem (the same problem in at least two different runs, inside the last 90 days) into a small, concrete
change to that workflow's YAML, by fixed rules, with the evidence next to it:

=======================================  ========================================================================
 a step ran out of time in 2+ runs       raise that step's ``timeout_min`` (by half, at most a day)
 a step failed or hit a backend error    give it one more ``retries``
 the evidence check let missing output   switch that step's ``evidence`` from ``advisory`` to ``required``
 through in 2+ runs
 reviewers rejected the same finding     add it to the rework target's ``acceptance`` list
=======================================  ========================================================================

Nothing here changes a workflow by itself. :func:`propose` writes a proposal file (a diff, the reasons, the number of
runs); :func:`apply` needs the proposal's id, re-validates the result, backs the file up (``agflow fix --undo`` reverts
it) and writes a *workspace copy* of the workflow, so a bundled workflow in the plugin is never edited. A change the
user rejected is not proposed again for 30 days.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import Any

from agflow import fixes, fsutil, learn, paths
from agflow.graph import catalog
from agflow.graph.schema import Graph, GraphError, parse_graph

MIN_RUNS = 2
REJECT_COOLDOWN_DAYS = 30.0
MAX_CHANGES = 6
TIMEOUT_CAP_MIN = 24 * 60
RETRY_CAP = 3
DAY = 86400.0
STATUSES = ("pending", "applied", "rejected", "undone")


class ImproveError(RuntimeError):
    """A proposal cannot be made, applied or undone; the message says why and what to do."""


@dataclass
class Change:
    id: str
    node: str
    field: str          # timeout_min | retries | evidence | acceptance (append)
    old: Any
    new: Any
    reason: str
    runs: int

    @property
    def signature(self) -> str:
        return f"{self.node}|{self.field}|{json.dumps(self.new, sort_keys=True)}"

    def describe(self) -> str:
        if self.field == "acceptance":
            what = f"add the criterion {self.new!r}"
        else:
            what = f"{self.field}: {self.old!r} -> {self.new!r}"
        return f"{self.node}: {what}  ({self.reason})"

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "node": self.node, "field": self.field, "old": self.old, "new": self.new,
                "reason": self.reason, "runs": self.runs}

    @classmethod
    def from_json(cls, data: Any) -> Change | None:
        try:
            return cls(str(data["id"]), str(data["node"]), str(data["field"]), data.get("old"), data["new"],
                       str(data.get("reason", "")), int(data.get("runs", 0)))
        except (KeyError, TypeError, ValueError, AttributeError):
            return None


@dataclass
class Proposal:
    id: str
    graph: str
    source: str                # the file the current workflow comes from
    target: str                # the file applying writes (the workspace copy)
    base_hash: str
    changes: list[Change]
    diff: str
    created_at: float
    status: str = "pending"
    applied_at: float | None = None
    applied_hash: str | None = None
    applied_changes: list[str] = field(default_factory=list)
    txn_ops: list[dict[str, Any]] = field(default_factory=list)
    txn_id: str | None = None
    note: str = ""

    def to_json(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        data["changes"] = [c.to_json() for c in self.changes]
        return data

    @classmethod
    def from_json(cls, data: Any) -> Proposal | None:
        try:
            changes = [c for c in map(Change.from_json, data["changes"]) if c is not None]
            base = {k: data[k] for k in ("id", "graph", "source", "target", "base_hash", "diff", "created_at")}
            extra = {k: data[k] for k in ("status", "applied_at", "applied_hash", "applied_changes", "txn_ops",
                                          "txn_id", "note") if k in data}
            if extra.get("status", "pending") not in STATUSES:
                return None
            return cls(changes=changes, **base, **extra)
        except (KeyError, TypeError):
            return None


# --- the rules (pure) -----------------------------------------------------------------------------


def suggest(graph: Graph, lessons: list[learn.Lesson], *, now: float,
            skip: frozenset[str] | set[str] = frozenset()) -> list[Change]:
    """The changes the lessons justify for ``graph``, strongest evidence first; ``skip`` holds signatures to omit."""
    found = [p for p in learn.patterns(lessons, now=now) if p.runs >= MIN_RUNS]
    out: list[Change] = []

    def add(node: str, fld: str, old: Any, new: Any, reason: str, runs: int) -> None:
        change = Change(f"c{len(out) + 1}", node, fld, old, new, reason, runs)
        if change.signature not in skip and all(change.signature != c.signature for c in out):
            out.append(change)

    for pat in found:
        try:
            node = graph.node(pat.node) if pat.node else None
        except KeyError:
            node = None  # a step that has since been removed from the workflow
        if node is None or node.type != "agent":
            continue
        if pat.kind == "timeout":
            current = graph.timeout_for(node)
            longer = min(TIMEOUT_CAP_MIN, max(current + 10, math.ceil(current * 1.5)))
            if longer > current:
                add(node.id, "timeout_min", current, longer,
                    f"ran out of time in {pat.runs} runs (limit {current} min)", pat.runs)
        elif pat.kind in ("failed_report", "backend_error") and graph.retries_for(node) < RETRY_CAP:
            add(node.id, "retries", graph.retries_for(node), graph.retries_for(node) + 1,
                f"failed in {pat.runs} runs: {pat.detail[:80]}", pat.runs)
        elif (pat.kind == "evidence_warning" and node.outputs and graph.evidence_for(node) == "advisory"
              and any(word in pat.detail for word in ("does not exist", "is empty", "no non-empty"))):
            add(node.id, "evidence", "advisory", "required",
                f"the evidence check flagged missing output in {pat.runs} runs but let it through", pat.runs)
        elif pat.kind == "review_rejected":
            for target in _rework_targets(graph, node.id):
                text = f"Addresses a finding reviewers raised in {pat.runs} earlier runs: {pat.detail[:160]}"
                if text not in (graph.node(target).acceptance or []):
                    add(target, "acceptance", None, text, f"{node.id} rejected the same finding in {pat.runs} runs",
                        pat.runs)
    return out[:MAX_CHANGES]


def _rework_targets(graph: Graph, reviewer: str) -> list[str]:
    """The nodes a failing gate sends back, for gates that wait on ``reviewer`` (directly or further up)."""
    from agflow.graph.schema import ancestors

    return [n.on_fail.goto for n in graph.nodes
            if n.type == "gate" and n.on_fail and (reviewer in ancestors(graph, n.id) or reviewer in n.depends_on)
            and graph.node(n.on_fail.goto).type == "agent"]


# --- editing the YAML -----------------------------------------------------------------------------


def edit(text: str, changes: list[Change]) -> str:
    """``text`` with ``changes`` applied, keeping its comments and layout."""
    yaml = fsutil.yaml_rt()
    data = yaml.load(text)
    nodes = {n["id"]: n for n in data["nodes"]}
    for change in changes:
        node = nodes.get(change.node)
        if node is None:
            raise ImproveError(f"the workflow has no step '{change.node}' any more")
        if change.field == "acceptance":
            if node.get("acceptance") is None:
                node["acceptance"] = [change.new]
            else:
                node["acceptance"].append(change.new)
        else:
            node[change.field] = change.new
    buf = StringIO()
    yaml.dump(data, buf)
    return buf.getvalue()


def _norm(text: str) -> str:
    return text.replace("\r\n", "\n")


def _hash(text: str) -> str:
    return hashlib.sha256(_norm(text).encode("utf-8")).hexdigest()


def _validated(text: str, source: str) -> Graph:
    try:
        return parse_graph(fsutil.yaml_safe().load(text), source)
    except (GraphError, ValueError) as exc:
        raise ImproveError(f"the changed workflow would not be valid ({exc}); nothing was written") from None


# --- storage --------------------------------------------------------------------------------------


def _folder(workspace: Path, graph_id: str) -> Path:
    folder = learn.workflow_dir(workspace, graph_id)
    if folder is None:
        raise ImproveError(f"'{graph_id}' is not a workflow id")
    return folder / "proposals"


def _save(workspace: Path, proposal: Proposal) -> None:
    fsutil.write_json(_folder(workspace, proposal.graph) / f"{proposal.id}.json", proposal.to_json())


def proposals(workspace: Path, graph_id: str | None = None) -> list[Proposal]:
    """Every proposal on file (for one workflow, or all), newest first. Damaged files are skipped."""
    base = paths.workflows_state_dir(workspace)
    folders = [_folder(workspace, graph_id)] if graph_id else sorted(base.glob("*/proposals"))
    found = []
    for folder in folders:
        for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            try:
                item = Proposal.from_json(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
            if item is not None:
                found.append(item)
    return sorted(found, key=lambda p: p.created_at, reverse=True)


def get(workspace: Path, proposal_id: str) -> Proposal:
    for item in proposals(workspace):
        if item.id == proposal_id:
            return item
    raise ImproveError(f"no proposal '{proposal_id}' (see `agflow improve list`)")


def current_status(workspace: Path, proposal: Proposal) -> str:
    """The proposal's status, noting an applied one whose file has been changed or restored since."""
    if proposal.status != "applied":
        return proposal.status
    try:
        now = _hash(Path(proposal.target).read_text(encoding="utf-8"))
    except OSError:
        return "applied (file removed since)"
    return "applied" if now == proposal.applied_hash else "applied (file changed since)"


# --- the commands ---------------------------------------------------------------------------------


def _current(workspace: Path, graph_id: str) -> tuple[Graph, Path, str]:
    try:
        graph, path = catalog.resolve(graph_id, workspace)
    except GraphError as exc:
        raise ImproveError(str(exc)) from None
    return graph, path, _norm(path.read_text(encoding="utf-8"))


def _target_for(workspace: Path, graph: Graph, source: Path) -> Path:
    graphs_dir = paths.workspace_layout(workspace).graphs_dir
    assert graphs_dir is not None
    try:
        source.resolve().relative_to(graphs_dir.resolve())
        return source.resolve()
    except ValueError:
        return graphs_dir / f"{graph.id}.yaml"


def propose(workspace: Path, graph_id: str, *, now: float | None = None) -> Proposal | None:
    """Write a proposal for ``graph_id`` if its lessons justify one; ``None`` when nothing recurs enough. Changes no
    workflow. The same set of changes proposed twice is one proposal."""
    moment = time.time() if now is None else now
    graph, source, text = _current(workspace, graph_id)
    previous = proposals(workspace, graph.id)
    recent = [p for p in previous if p.status == "rejected" and moment - p.created_at < REJECT_COOLDOWN_DAYS * DAY]
    cooled = {c.signature for p in recent for c in p.changes}
    changes = suggest(graph, learn.load_lessons(workspace, graph.id), now=moment, skip=cooled)
    if not changes:
        return None
    for index, change in enumerate(changes, 1):
        change.id = f"c{index}"
    new_text = edit(text, changes)
    _validated(new_text, str(source))
    target = _target_for(workspace, graph, source)
    base_hash = _hash(text)
    pid = hashlib.sha1((graph.id + base_hash + "".join(sorted(c.signature for c in changes))).encode()).hexdigest()[:10]
    existing = next((p for p in previous if p.id == pid), None)
    if existing is not None and existing.status in ("pending", "applied"):
        return existing
    diff = "".join(difflib.unified_diff(text.splitlines(True), new_text.splitlines(True),
                                        f"{graph.id} (current)", f"{graph.id} (proposed)"))
    proposal = Proposal(pid, graph.id, str(source), str(target), base_hash, changes, diff, moment)
    _save(workspace, proposal)
    return proposal


def apply(workspace: Path, proposal_id: str, *, only: list[str] | None = None, now: float | None = None) -> Proposal:
    """Apply a pending proposal (all of its changes, or just the ids in ``only``) as a workspace copy of the workflow.
    Backed up through the fix history: ``agflow fix --undo`` or :func:`undo` reverts it."""
    moment = time.time() if now is None else now
    proposal = get(workspace, proposal_id)
    if proposal.status != "pending":
        raise ImproveError(f"proposal {proposal_id} is {proposal.status}, not pending")
    chosen = [c for c in proposal.changes if only is None or c.id in only]
    if not chosen:
        raise ImproveError(f"none of {only} is a change of this proposal ({', '.join(c.id for c in proposal.changes)})")
    graph, source, text = _current(workspace, proposal.graph)
    if _hash(text) != proposal.base_hash:
        raise ImproveError("the workflow changed since this was proposed; run `agflow improve propose` again")
    new_text = edit(text, chosen)
    _validated(new_text, str(source))
    target = Path(proposal.target)
    txn = fixes.Txn()
    txn.write(target, new_text)
    txn.commit(workspace, [f"improve {proposal.graph}: " + "; ".join(c.describe() for c in chosen)])
    proposal.status, proposal.applied_at, proposal.applied_hash = "applied", moment, _hash(new_text)
    proposal.applied_changes = [c.id for c in chosen]
    proposal.txn_ops, proposal.txn_id = txn.ops, txn.id
    _save(workspace, proposal)
    return proposal


def reject(workspace: Path, proposal_id: str, reason: str = "") -> Proposal:
    proposal = get(workspace, proposal_id)
    if proposal.status != "pending":
        raise ImproveError(f"proposal {proposal_id} is {proposal.status}, not pending")
    proposal.status, proposal.note = "rejected", learn.clean(reason)
    _save(workspace, proposal)
    return proposal


def undo(workspace: Path, graph_id: str, *, force: bool = False) -> Proposal:
    """Revert the most recently applied proposal of ``graph_id``. Refuses if the file was edited since (``force``
    discards those edits)."""
    applied = [p for p in proposals(workspace, graph_id) if p.status == "applied"]
    if not applied:
        raise ImproveError(f"nothing applied to '{graph_id}' that can be undone")
    proposal = max(applied, key=lambda p: p.applied_at or 0.0)
    target = Path(proposal.target)
    if not force and target.is_file() and _hash(target.read_text(encoding="utf-8")) != proposal.applied_hash:
        raise ImproveError(f"{target} was edited after the change was applied; undoing would discard those edits "
                           "(repeat with --force if that is what you want)")
    fixes.restore_ops(proposal.txn_ops)
    fixes.drop_batch(proposal.txn_id)
    proposal.status = "undone"
    _save(workspace, proposal)
    return proposal
