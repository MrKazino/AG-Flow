"""Find graphs by id across workspace, installed plugins, global and bundled locations."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path

from agflow import paths
from agflow.graph.schema import Graph, GraphError, load_graph
from agflow.inventory import iter_graphs


@dataclass
class GraphRef:
    id: str
    path: Path
    source: str
    title: str = ""
    description: str = ""
    error: str = ""


def search_dirs(workspace: Path | None) -> list[tuple[Path, str]]:
    dirs: list[tuple[Path, str]] = []
    if workspace is not None:
        dirs.append((paths.workspace_layout(workspace).graphs_dir, "workspace"))  # type: ignore[arg-type]
    for pdir in paths.installed_plugin_dirs(workspace):
        dirs.append((pdir / "graphs", paths.plugin_scope_label(pdir, workspace)))
    dirs.append((paths.global_layout().graphs_dir, "global"))  # type: ignore[arg-type]
    with contextlib.suppress(FileNotFoundError):
        dirs.append((paths.bundled_plugin_dir() / "graphs", "bundled"))
    return dirs


def list_graphs(workspace: Path | None) -> list[GraphRef]:
    seen: dict[str, GraphRef] = {}
    for d, source in search_dirs(workspace):
        for p in iter_graphs(d):
            try:
                g = load_graph(p)
                ref = GraphRef(g.id, p, source, g.title, g.description)
            except GraphError as exc:
                ref = GraphRef(p.stem, p, source, error=str(exc))
            seen.setdefault(ref.id, ref)
    return sorted(seen.values(), key=lambda r: r.id)


def resolve(graph: str, workspace: Path | None) -> tuple[Graph, Path]:
    """Accept a graph id or a path to a YAML file."""
    candidate = Path(graph).expanduser()
    if candidate.suffix in (".yaml", ".yml") or candidate.is_file():
        if not candidate.is_absolute() and workspace is not None and not candidate.exists():
            candidate = workspace / candidate
        if not candidate.is_file():
            raise GraphError(f"graph file not found: {graph}")
        return load_graph(candidate), candidate.resolve()
    for ref in list_graphs(workspace):
        if ref.id == graph:
            if ref.error:
                raise GraphError(ref.error)
            return load_graph(ref.path), ref.path
    available = ", ".join(r.id for r in list_graphs(workspace)) or "none"
    raise GraphError(f"unknown graph '{graph}'. Available: {available}")
