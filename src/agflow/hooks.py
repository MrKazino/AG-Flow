"""Antigravity hook handlers: `agflow hook pre-tool-use | post-tool-use | stop`.

Antigravity runs a hook command with a JSON payload on stdin and reads a JSON decision from
stdout. The payload schema is not formally documented, so every accessor below is defensive.

* PreToolUse  -> {"decision": "deny"|"ask", "reason": ...} for dangerous actions, else {} (no opinion;
                 we never return "allow", which could bypass the user's own permission settings).
* PostToolUse -> {} ; appends a compact entry to the ledger of every active agflow run.
* Stop        -> {"decision": "continue", "reason": ...} while an active run still has claimable
                 nodes and is making progress; otherwise {}.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agflow import paths
from agflow.fsutil import read_yaml

MAX_STOP_CONTINUES_WITHOUT_PROGRESS = 2

_TOOL_KEYS = ("tool_name", "toolName", "tool", "name")
_ARGS_KEYS = ("tool_input", "toolInput", "args", "arguments", "input", "parameters")
_CMD_KEYS = ("CommandLine", "command_line", "commandLine", "command", "cmd")
_PATH_KEYS = ("AbsolutePath", "TargetFile", "FilePath", "file_path", "path", "filePath", "target_file")


@dataclass(frozen=True)
class GuardRule:
    decision: str  # "deny" | "ask"
    pattern: re.Pattern[str]
    reason: str


def _r(decision: str, pattern: str, reason: str) -> GuardRule:
    return GuardRule(decision, re.compile(pattern, re.IGNORECASE), reason)


COMMAND_RULES: tuple[GuardRule, ...] = (
    _r("deny", r"\brm\s+(-[a-z]*\s+)*-[a-z]*r[a-z]*f?[a-z]*\s+(--no-preserve-root\s+)?"
               r"(/|/\*|~|~/|\$HOME|\$\{HOME\})(\s|$)",
       "recursive delete of the filesystem root or home directory"),
    _r("deny", r"\bmkfs(\.\w+)?\b", "formats a filesystem"),
    _r("deny", r"\bdd\b.*\bof=/dev/(sd|nvme|disk|hd)", "writes raw bytes to a disk device"),
    _r("deny", r":\(\)\s*\{\s*:\|:&\s*\};:", "fork bomb"),
    _r("deny", r"\bchmod\s+(-R\s+)?777\s+/(\s|$)", "makes the filesystem root world-writable"),
    _r("deny", r">\s*/dev/(sd|nvme|disk)", "overwrites a disk device"),
    _r("ask", r"\bgit\s+push\b.*(--force\b|--force-with-lease\b|\s-f\b)", "force-push rewrites remote history"),
    _r("ask", r"\bgit\s+reset\s+--hard\b", "discards uncommitted work"),
    _r("ask", r"\bgit\s+clean\s+-[a-z]*f", "deletes untracked files"),
    _r("ask", r"\bgit\s+(checkout|restore)\s+(--\s+)?\.(\s|$)", "discards all local changes"),
    _r("ask", r"\bgit\s+branch\s+-D\b", "force-deletes a branch"),
    _r("ask", r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba|z|da)?sh\b", "pipes a remote script into a shell"),
    _r("ask", r"\brm\s+(?:-\S+\s+)*-[a-z]*r[a-z]*\b[^;&|]*?\s(?:/|~|\.\./|\$HOME|\$\{HOME\})",
       "recursive delete outside the workspace"),
    _r("ask", r"\b(drop\s+(table|database|schema)|truncate\s+table)\b", "destructive SQL"),
    _r("ask", r"\bterraform\s+(destroy|apply\b.*-auto-approve)", "destructive infrastructure change"),
    _r("ask", r"\bkubectl\s+delete\b", "deletes cluster resources"),
    _r("ask", r"\b(npm|pnpm|yarn)\s+publish\b|\btwine\s+upload\b|\bcargo\s+publish\b", "publishes a package"),
    _r("ask", r"\bdocker\s+(system|volume)\s+prune\b", "deletes docker data"),
    _r("ask", r"\bsudo\b", "runs with elevated privileges"),
    # --- Windows (cmd.exe / PowerShell) ---
    _r("deny", r"\bformat(\.com)?\s+[a-z]:", "formats a drive"),
    _r("deny", r"\b(rd|rmdir|del|erase|remove-item|ri|rm)\b(?=[^|&;]*(\s/s\b|\s-r(ecurse)?\b))"
               r"(?=[^|&;]*\s[\"']?([a-z]:[\\/]?|%systemdrive%[\\/]?|%userprofile%[\\/]?|"
               r"\$env:(userprofile|systemdrive)[\\/]?|\$home[\\/]?)[\"']?(\s|$))",
       "recursive delete of a drive root or the user profile"),
    _r("ask", r"\b(rd|rmdir|del|erase|remove-item|ri)\b(?=[^|&;]*(\s/s\b|\s-recurse\b))"
              r"(?=[^|&;]*(\s[\"']?[a-z]:[\\/]|\.\.[\\/]|\$env:|%[a-z_]+%))",
       "recursive delete outside the workspace"),
    _r("ask", r"\b(iwr|irm|invoke-webrequest|invoke-restmethod|curl|wget)\b[^|]*\|\s*(iex|invoke-expression)\b",
       "pipes a remote script into PowerShell"),
    _r("ask", r"-verb\s+runas\b", "runs with elevated privileges"),
    _r("ask", r"\b(diskpart|bcdedit)\b|\breg(\.exe)?\s+delete\b", "changes disks, boot or registry settings"),
)

SECRET_PATH = re.compile(
    r"(^|/)(\.env(\.[\w-]+)?|id_(rsa|ed25519|ecdsa)|[^/]+\.(pem|key|p12|pfx)|credentials(\.json)?|"
    r"\.npmrc|\.pypirc|\.netrc|service[-_]account[^/]*\.json)$",
    re.IGNORECASE,
)
SECRET_PATH_ALLOW = re.compile(r"\.env\.(example|sample|template|dist)$", re.IGNORECASE)


# --- payload helpers ----------------------------------------------------------------------


def _first(d: dict[str, Any], keys: Iterable[str]) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def tool_name(payload: dict[str, Any]) -> str:
    name = _first(payload, _TOOL_KEYS)
    if isinstance(name, dict):
        name = _first(name, ("name",))
    return str(name or "")


def tool_args(payload: dict[str, Any]) -> dict[str, Any]:
    args = _first(payload, _ARGS_KEYS)
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"command": args}
    return args if isinstance(args, dict) else {}


def payload_workspace(payload: dict[str, Any]) -> Path:
    ws = _first(payload, ("workspace", "workspacePath", "workspace_root", "cwd", "project_dir"))
    if isinstance(ws, list) and ws:
        ws = ws[0]
    if isinstance(ws, str) and ws.startswith("file://"):
        parsed = urllib.parse.urlparse(ws)
        ws = urllib.request.url2pathname(parsed.path)  # file:///C:/x -> C:\x on Windows
    return paths.find_workspace(Path(ws) if ws else None)


# --- guard ------------------------------------------------------------------------------------


def load_guard_overrides(workspace: Path) -> tuple[list[GuardRule], list[re.Pattern[str]]]:
    """Optional <ws>/.agents/agflow/guard.yaml: {deny: [regex], ask: [regex], allow: [regex]}."""
    data = read_yaml(paths.workspace_state_dir(workspace) / "guard.yaml", {}) or {}
    extra = [_r("deny", p, "matches guard.yaml deny rule") for p in data.get("deny", []) or []]
    extra += [_r("ask", p, "matches guard.yaml ask rule") for p in data.get("ask", []) or []]
    allow = [re.compile(p, re.IGNORECASE) for p in data.get("allow", []) or []]
    return extra, allow


def check_command(command: str, extra: list[GuardRule] = (), allow: list[re.Pattern[str]] = ()) -> dict[str, Any]:
    if any(a.search(command) for a in allow):
        return {}
    for rule in (*extra, *COMMAND_RULES):
        if rule.pattern.search(command):
            return {"decision": rule.decision, "reason": f"agflow guard: {rule.reason} — `{command[:200]}`"}
    return {}


def check_path(path: str) -> dict[str, Any]:
    normalized = path.replace("\\", "/")  # Windows paths
    if SECRET_PATH.search(normalized) and not SECRET_PATH_ALLOW.search(normalized):
        return {"decision": "ask", "reason": f"agflow guard: {path} looks like a secret/credential file"}
    return {}


def pre_tool_use(payload: dict[str, Any]) -> dict[str, Any]:
    if os.environ.get("AGFLOW_GUARD", "on").lower() in ("off", "0", "false"):
        return {}
    args = tool_args(payload)
    extra, allow = load_guard_overrides(payload_workspace(payload))
    command = _first(args, _CMD_KEYS)
    if isinstance(command, list):
        command = " ".join(map(str, command))
    if isinstance(command, str) and command.strip():
        decision = check_command(command, extra, allow)
        if decision:
            return decision
    target = _first(args, _PATH_KEYS)
    if isinstance(target, str) and target:
        return check_path(target)
    return {}


# --- ledger / stop ------------------------------------------------------------------------------


def post_tool_use(payload: dict[str, Any]) -> dict[str, Any]:
    from agflow.graph.store import RunStore

    try:
        store = RunStore(payload_workspace(payload))
        active = store.active()
    except Exception:
        return {}
    if not active:
        return {}
    args = tool_args(payload)
    brief = _first(args, _CMD_KEYS) or _first(args, _PATH_KEYS) or ""
    entry = {"event": "tool", "tool": tool_name(payload), "detail": str(brief)[:300]}
    for run in active:
        try:
            store.ledger(run.run_id, entry)
        except OSError:
            continue
    return {}


def stop(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep the orchestrator going while an active run has claimable work and is making progress."""
    from agflow.graph import engine
    from agflow.graph.store import RunStore

    try:
        store = RunStore(payload_workspace(payload))
        active = store.active()
    except Exception:
        return {}
    for run in active:
        with store.transaction(run.run_id) as state:
            g = state.graph_model()
            engine.recover_stale(g, state, now=time.time())
            claimable = [n for n in engine.ready(g, state) if g.node(n).type == "agent"]
            if not claimable:
                continue
            marker = engine.progress_marker(state)
            if marker != state.progress_marker:
                state.progress_marker = marker
                state.stop_continues = 0
            if state.stop_continues >= MAX_STOP_CONTINUES_WITHOUT_PROGRESS:
                continue
            state.stop_continues += 1
        return {
            "decision": "continue",
            "reason": (f"agflow run {run.run_id} ({run.graph_id}) still has ready nodes: "
                       f"{', '.join(claimable)}. Call graph_next with run_id={run.run_id} and dispatch them, "
                       "or graph_abort if the user asked to stop."),
        }
    return {}


HANDLERS = {
    "pre-tool-use": pre_tool_use,
    "post-tool-use": post_tool_use,
    "stop": stop,
}


def run_hook(event: str, stdin: str | None = None) -> str:
    raw = sys.stdin.read() if stdin is None else stdin
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if os.environ.get("AGFLOW_HOOK_DEBUG"):
        _debug_log(event, payload)
    try:
        result = HANDLERS[event](payload)
    except Exception as exc:  # a hook must never break the agent
        _debug_log(event, {"error": repr(exc), "payload": payload})
        result = {}
    return json.dumps(result)


def _debug_log(event: str, payload: dict[str, Any]) -> None:
    try:
        f = paths.global_state_dir() / "hook-debug.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time(), "event": event, "payload": payload}, default=str)[:20000] + "\n")
    except OSError:
        pass
