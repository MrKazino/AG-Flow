"""Inventory data model: one record per discovered component."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class Kind(str, Enum):
    SKILL = "skill"
    AGENT = "agent"
    RULE = "rule"
    GEMINI_MD = "gemini_md"
    WORKFLOW = "workflow"
    PLUGIN = "plugin"
    MCP = "mcp"
    HOOK = "hook"
    GRAPH = "graph"


# Kinds that can be parked/unparked by `agflow <kind> enable|disable`.
TOGGLEABLE = (Kind.SKILL, Kind.AGENT, Kind.RULE)


@dataclass
class Item:
    kind: Kind
    name: str
    path: Path
    scope: str  # "global" | "workspace" | "plugin:<name>" | "legacy"
    description: str = ""
    enabled: bool = True
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["path"] = str(self.path)
        return d


class Level(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class Issue:
    level: Level
    code: str
    message: str
    path: Path | None = None
    source: str = ""  # where it comes from: "plugin:agflow", "plugin:<other>", "global", "workspace", "legacy"

    @property
    def id(self) -> str:
        """Stable identifier (same issue -> same id across runs)."""
        import hashlib

        key = f"{self.code}|{self.path or ''}|{self.message}"
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "level": self.level.value,
            "code": self.code,
            "message": self.message,
            "path": str(self.path) if self.path else None,
            "source": self.source,
        }

    def __str__(self) -> str:
        where = f" [{self.path}]" if self.path else ""
        return f"{self.level.value.upper():7} {self.code}: {self.message}{where}"
