"""Read-only analysis of a GEMINI.md: where the characters go and what to trim.

Antigravity caps rules files (GEMINI.md included) at 12,000 characters. This module never writes
anything; `agflow gemini-md` prints its report.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from agflow.paths import GEMINI_MD_CHAR_LIMIT

_BLOCK = re.compile(r"<!--\s*([\w.-]+):begin\b.*?-->.*?<!--\s*\1:end\s*-->", re.DOTALL | re.IGNORECASE)
_HEADING = re.compile(r"^(#{1,2})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def _sections(text: str) -> list[dict[str, Any]]:
    """Split on level-1/2 headings outside code fences."""
    sections: list[dict[str, Any]] = []
    current = {"title": "(before the first heading)", "line": 1, "chars": 0, "code_chars": 0}
    in_fence = False
    for n, line in enumerate(text.splitlines(keepends=True), start=1):
        if _FENCE.match(line):
            in_fence = not in_fence
            current["code_chars"] += len(line)
        elif in_fence:
            current["code_chars"] += len(line)
        else:
            m = _HEADING.match(line.rstrip("\n"))
            if m:
                if current["chars"]:
                    sections.append(current)
                current = {"title": m.group(2).strip(), "line": n, "chars": 0, "code_chars": 0}
        current["chars"] += len(line)
    if current["chars"]:
        sections.append(current)
    return sections


def analyze(text: str, limit: int = GEMINI_MD_CHAR_LIMIT) -> dict[str, Any]:
    total = len(text)
    blocks = []
    for m in _BLOCK.finditer(text):
        name = m.group(1)
        if name.lower() == "agflow":
            note = ("agflow master prompt; if GEMINI.md is too large, `agflow install` uses an always-on "
                    "plugin rule instead, so this block can go")
        else:
            note = f"written by '{name}'; change it in that tool rather than by hand"
        blocks.append({"name": name, "chars": len(m.group(0)), "note": note})

    sections = _sections(text)
    for s in sections:
        s["share"] = s["chars"] / total if total else 0.0
    sections.sort(key=lambda s: s["chars"], reverse=True)

    over_by = total - limit
    advice: list[str] = []
    if over_by > 0:
        advice.append(f"Cut at least {over_by:,} characters to get under the {limit:,}-character limit; "
                      "Antigravity may not load everything past it.")
        big = [s["title"] for s in sections[:3] if s["share"] >= 0.08]
        if big:
            advice.append("Largest sections: " + ", ".join(f"'{t}'" for t in big) + ". Keep only rules that apply "
                          "to every task here; move situational guidance into `.agents/rules/*.md` with "
                          "`trigger: model_decision` (or `glob`) or into a skill, so it loads only when relevant.")
    code = sum(s["code_chars"] for s in sections)
    if code > 0.2 * max(total, 1):
        advice.append(f"{code:,} characters are code blocks; long examples rarely need to be always-on — move "
                      "them into a skill's references/ folder.")
    dupes = [t for t, n in Counter(s["title"].lower() for s in sections).items() if n > 1]
    if dupes:
        advice.append("Repeated headings (possible duplication): " + ", ".join(f"'{d}'" for d in sorted(dupes)) + ".")
    if not advice:
        advice.append("Within the limit; nothing to trim.")
    return {"chars": total, "limit": limit, "over_by": over_by, "blocks": blocks, "sections": sections,
            "advice": advice}


def segments(text: str) -> list[dict[str, Any]]:
    """Split text into contiguous segments at level-1/2 headings (outside code fences) and at the edges of
    tool-managed blocks. Each segment: {start, end, title, heading, block}. `block` is the managing tool's
    name for text inside `<!-- x:begin -->…<!-- x:end -->`, else None. Segments cover the text exactly."""
    blocks = [(m.start(), m.end(), m.group(1)) for m in _BLOCK.finditer(text)]
    cuts = {0, len(text)}
    headings: dict[int, str] = {}
    offset, in_fence = 0, False
    for line in text.splitlines(keepends=True):
        if _FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence:
            m = _HEADING.match(line.rstrip("\r\n"))
            if m:
                cuts.add(offset)
                headings[offset] = m.group(2).strip()
        offset += len(line)
    for start, end, _ in blocks:
        cuts.update((start, end))
    points = sorted(cuts)
    out = []
    for start, end in zip(points, points[1:], strict=False):
        if start == end:
            continue
        block = next((name for bs, be, name in blocks if bs <= start and end <= be), None)
        out.append({"start": start, "end": end, "title": headings.get(start, ""), "heading": start in headings,
                    "block": block})
    return out
