"""Manage MCP servers in an Antigravity ``mcp_config.json`` (atomic writes + backups)."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

from agflow import paths
from agflow.fsutil import backup, read_json, timestamp, write_json

_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Curated, commonly used servers (commands only; secrets are always ${ENV} placeholders).
CATALOG: dict[str, dict[str, Any]] = {
    "github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
               "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_PERSONAL_ACCESS_TOKEN}"}},
    "chrome-devtools": {"command": "npx", "args": ["-y", "chrome-devtools-mcp@latest"]},
    "playwright": {"command": "npx", "args": ["-y", "@playwright/mcp@latest"]},
    "context7": {"command": "npx", "args": ["-y", "@upstash/context7-mcp"]},
    "sequential-thinking": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"]},
    "memory": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"]},
    "postgres": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-postgres", "${DATABASE_URL}"]},
    "firebase": {"command": "npx", "args": ["-y", "firebase-tools@latest", "mcp"]},
}


class McpError(ValueError):
    pass


def config_file(scope: str, workspace: Path | None) -> Path:
    if scope == "global":
        return paths.global_layout().mcp_file  # type: ignore[return-value]
    if scope == "workspace":
        if workspace is None:
            raise McpError("workspace scope needs a workspace")
        return paths.workspace_layout(workspace).mcp_file  # type: ignore[return-value]
    if scope == "agy-cli":
        return paths.agy_cli_dir() / "mcp_config.json"
    raise McpError(f"unknown scope {scope!r} (global | workspace | agy-cli)")


def _load(f: Path) -> dict[str, Any]:
    """Read ``f`` and check its shape, so a hand-edited file produces a readable error naming it instead of a
    ``TypeError``/``AttributeError`` from deep inside an operation (callers turn ``McpError`` into messages/400s)."""
    try:
        data = read_json(f, {})
    except (OSError, ValueError) as exc:  # unreadable, not UTF-8, or not JSON
        raise McpError(f"{f} could not be read as JSON: {exc}") from exc
    if data is None:  # a literal `null`
        data = {}
    if not isinstance(data, dict):  # checked before any truthiness shortcut: `[] or {}` must not pass as valid
        raise McpError(f"{f} is not a JSON object")
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise McpError(f"'mcpServers' in {f} must be an object of server entries, got {type(servers).__name__}")
    bad = sorted(name for name, cfg in servers.items() if not isinstance(cfg, dict))
    if bad:
        raise McpError(f"{f}: server entr{'y' if len(bad) == 1 else 'ies'} {', '.join(map(repr, bad))} must be "
                       "an object (command/args/env or serverUrl)")
    return data


def _save(f: Path, data: dict[str, Any]) -> None:
    if f.exists():
        backup(f, paths.backups_dir() / timestamp(), paths.home())
    write_json(f, data)


def list_servers(f: Path) -> dict[str, dict[str, Any]]:
    return dict(_load(f)["mcpServers"])


def add_server(f: Path, name: str, *, command: str | None = None, args: list[str] | None = None,
               env: dict[str, str] | None = None, url: str | None = None, replace: bool = False) -> dict[str, Any]:
    if not re.match(r"^[A-Za-z0-9_.-]+$", name):
        raise McpError("server name may only contain letters, digits, '.', '_' and '-'")
    if command and url:
        raise McpError("give only one of --command (stdio) or --url (http)")
    if command:
        spec: dict[str, Any] = {"command": command, "args": list(args or [])}
    elif url:
        spec = {"serverUrl": url}
    elif name in CATALOG:
        spec = copy.deepcopy(CATALOG[name])
    else:
        raise McpError(f"'{name}' is not in the catalog ({', '.join(sorted(CATALOG))}); "
                       "give --command (stdio) or --url (http)")
    if env:
        spec["env"] = {**spec.get("env", {}), **env}
    data = _load(f)
    if name in data["mcpServers"] and not replace:
        raise McpError(f"server '{name}' already exists (use --replace)")
    data["mcpServers"][name] = spec
    _save(f, data)
    return spec


def remove_server(f: Path, name: str) -> None:
    data = _load(f)
    if name not in data["mcpServers"]:
        raise McpError(f"no server named '{name}' in {f}")
    del data["mcpServers"][name]
    _save(f, data)


def set_enabled(f: Path, name: str, enabled: bool) -> None:
    data = _load(f)
    if name not in data["mcpServers"]:
        raise McpError(f"no server named '{name}' in {f}")
    if enabled:
        data["mcpServers"][name].pop("disabled", None)
    else:
        data["mcpServers"][name]["disabled"] = True
    _save(f, data)


def missing_env(spec: dict[str, Any], environ: dict[str, str]) -> list[str]:
    """Environment variables referenced as ${VAR} that are not set."""
    blob = " ".join([str(spec.get("command", "")), *map(str, spec.get("args", []) or []),
                     *map(str, (spec.get("env") or {}).values()), str(spec.get("serverUrl", ""))])
    return sorted({m for m in _ENV_REF.findall(blob) if m not in environ})
