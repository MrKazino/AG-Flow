"""Compile a graph into a SKILL.md so it can be launched as `/<graph-id>` in Antigravity."""

from __future__ import annotations

from pathlib import Path

from agflow import frontmatter
from agflow.fsutil import atomic_write_text
from agflow.graph.render import to_mermaid
from agflow.graph.schema import Graph


def graph_skill_markdown(graph: Graph) -> str:
    what = graph.description or graph.title or f"the {graph.id} workflow"
    description = (
        f"Run the '{graph.id}' agflow orchestration graph: {what.rstrip('.')}. Use when the user invokes "
        f"/{graph.id} or asks to run this multi-agent workflow end-to-end."
    )[:1024]
    lines = [f"# /{graph.id} — {graph.title or graph.id}", "", what, "", "## Inputs", ""]
    if graph.inputs:
        for name, spec in graph.inputs.items():
            req = "required" if spec.required else f"optional, default `{spec.default}`"
            lines.append(f"- `{name}` ({spec.type}, {req}){': ' + spec.description if spec.description else ''}")
    else:
        lines.append("- none")
    lines += [
        "",
        "## Procedure",
        "",
        "1. Collect every required input from the user's request. Ask one concise question only if a "
        "required input is genuinely missing.",
        f"2. Call the agflow MCP tool `graph_start` with `graph: \"{graph.id}\"`, `workspace`: the absolute "
        "workspace path, and the inputs.",
        "3. Follow the `graph-run` skill loop until the run finishes: `graph_next` → dispatch every card "
        "with `invoke_subagent` in parallel (pass `subagent_prompt` verbatim to the card's `agent`) → "
        "`graph_report` each result → repeat.",
        "4. For human nodes, ask the user the card's question and report their decision.",
        "5. Finish with a walkthrough: outcome, evidence, artifacts, follow-ups (from `graph_status`).",
        "",
        "## Graph",
        "",
        "```mermaid",
        to_mermaid(graph).rstrip(),
        "```",
    ]
    return frontmatter.dump({"name": graph.id, "description": description}, "\n".join(lines) + "\n")


def compile_to_skill(graph: Graph, skills_dir: Path) -> Path:
    target = skills_dir / graph.id / "SKILL.md"
    atomic_write_text(target, graph_skill_markdown(graph))
    return target
