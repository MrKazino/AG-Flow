"""The MCP tools are specified once (the `graph-run` skill's tool reference); every other document names a tool bare.

Before F30 the same tools were restated with different argument lists in six places (``graph_report(…, artifacts)``
here, ``(…, artifacts, followups)`` there; ``graph_runs(only_active=true)`` and ``graph_runs(workspace, only_active)``),
and nothing noticed when one drifted. This guard concerns the repository's own documents, not a user's configuration,
so it is a test rather than a doctor rule.
"""

from __future__ import annotations

import re

import pytest
from mcp import Client

from agflow.mcp_server import build_server
from tests.conftest import PLUGIN, REPO

CANONICAL = PLUGIN / "skills" / "graph-run" / "SKILL.md"
DOCS = sorted([*PLUGIN.rglob("*.md"), *(REPO / "prompts").glob("*.md"), REPO / "README.md"])

TOOL_REF = re.compile(r"(?<![\w-])(graph_[a-z]+)(\([^)\n]*\))?")
TABLE_ROW = re.compile(r"^\|\s*`(graph_[a-z]+)`\s*\|\s*`(graph_[a-z]+\([^`\n]*\))`\s*\|", re.M)


async def live_signatures() -> dict[str, str]:
    """``name(required, optional?)`` for every tool the server registers, in the order of its Python signature."""
    out = {}
    async with Client(build_server()) as client:
        for tool in (await client.list_tools()).tools:
            schema = getattr(tool, "input_schema", None) or tool.inputSchema
            required = set(schema.get("required", []))
            args = ", ".join(p if p in required else f"{p}?" for p in schema.get("properties", {}))
            out[tool.name] = f"{tool.name}({args})"
    return out


def drift(text: str, live: dict[str, str], *, allow_table: bool = False) -> list[str]:
    """What is wrong with the tool mentions in ``text``: unknown names, and argument lists that are not canonical."""
    if allow_table:
        text = TABLE_ROW.sub("", text)
    problems = []
    for match in TOOL_REF.finditer(text):
        name, call = match.group(1), match.group(2)
        if name not in live:
            problems.append(f"unknown tool {name!r}")
        elif call is not None and match.group(0) != live[name]:
            problems.append(f"{match.group(0)!r} is not the canonical {live[name]!r}")
    return problems


async def test_the_canonical_table_is_the_live_tool_list():
    live = await live_signatures()
    text = CANONICAL.read_text(encoding="utf-8")
    rows = TABLE_ROW.findall(text)
    names = [name for name, _ in rows]
    assert sorted(names) == sorted(set(names)), "a tool appears twice in the table"
    assert dict(rows) == live, "the tool reference in the graph-run skill no longer matches the server's tools"
    assert text.count("## Tool reference") == 1


async def test_no_document_restates_a_signature_differently():
    live = await live_signatures()
    problems = {}
    for path in DOCS:
        found = drift(path.read_text(encoding="utf-8"), live, allow_table=path == CANONICAL)
        if found:
            problems[str(path.relative_to(REPO))] = found
    assert problems == {}, problems


def test_the_documents_are_actually_scanned():
    names = {p.name for p in DOCS}
    assert {"SKILL.md", "GEMINI.md", "orchestrator.md", "orchestration.md", "orchestrate.md", "README.md"} <= names
    assert any("graph_start" in p.read_text(encoding="utf-8") for p in DOCS if p != CANONICAL)


LIVE = {
    "graph_report": "graph_report(run_id, node_id, status, summary?, artifacts?, followups?, workspace?)",
    "graph_runs": "graph_runs(workspace?, only_active?)",
    "graph_next": "graph_next(run_id, workspace?, max_cards?)",
}


@pytest.mark.parametrize(("text", "expected"), [
    ("call `graph_next` then `graph_report`", []),
    (f"use `{LIVE['graph_runs']}` to list", []),
    ("`graph_report(run_id, node_id, status, summary, artifacts)`", 1),   # the drift the audit found
    ("`graph_runs(only_active=true)`", 1),
    ("`graph_next(run_id)`", 1),
    ("then graph_nxt", 1),                                                  # a typo or a renamed tool
    ("a graph_id placeholder", 1),                                          # not a tool: spell it differently
    ("see the graph-run skill", []),
    ("graph_report(run_id, node_id, status, summary?, artifacts?, followups?, workspace?) and graph_next", []),
])
def test_the_drift_check_catches_what_it_claims_to(text, expected):
    found = drift(text, LIVE)
    assert (len(found) if isinstance(expected, int) else found) == expected, found
