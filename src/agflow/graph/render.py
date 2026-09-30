"""Mermaid rendering of graphs (optionally coloured by run state)."""

from __future__ import annotations

from agflow.graph.engine import RunState
from agflow.graph.schema import Graph

_STATUS_CLASS = {
    "succeeded": "fill:#1f7a3d,color:#fff,stroke:#145228",
    "failed": "fill:#b3261e,color:#fff,stroke:#7a1a14",
    "running": "fill:#1a5fb4,color:#fff,stroke:#123f78",
    "skipped": "fill:#9e9e9e,color:#fff,stroke:#6e6e6e",
    "blocked": "fill:#6d4c41,color:#fff,stroke:#4b342c",
    "pending": "fill:#f5f5f5,color:#222,stroke:#9e9e9e",
}


def _label(text: str) -> str:
    return text.replace('"', "'")


def to_mermaid(graph: Graph, state: RunState | None = None, direction: str = "TD") -> str:
    lines = [f"flowchart {direction}"]
    for n in graph.nodes:
        sub = n.agent if n.type == "agent" else n.type
        extra = f"<br/><i>/{n.skill}</i>" if n.skill else ""
        text = _label(f"{n.label}<br/><small>{sub}</small>{extra}")
        if n.type == "gate":
            lines.append(f'    {_id(n.id)}{{"{text}"}}')
        elif n.type == "human":
            lines.append(f'    {_id(n.id)}[/"{text}"/]')
        else:
            lines.append(f'    {_id(n.id)}["{text}"]')
    for n in graph.nodes:
        for d in n.depends_on:
            label = "|\"when " + _label(n.when).replace("|", "/") + "\"|" if n.when else ""
            lines.append(f"    {_id(d)} -->{label} {_id(n.id)}")
        if n.on_fail:
            lines.append(f"    {_id(n.id)} -.->|rework x{n.on_fail.max_loops}| {_id(n.on_fail.goto)}")
    if state is not None:
        for status, style in _STATUS_CLASS.items():
            lines.append(f"    classDef {status} {style}")
        for nid, ns in state.nodes.items():
            lines.append(f"    class {_id(nid)} {ns.status.value}")
    return "\n".join(lines) + "\n"


def _id(node_id: str) -> str:
    return "n_" + node_id.replace("-", "_")
