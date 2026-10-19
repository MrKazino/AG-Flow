"""Antigravity hook handlers: `agflow hook pre-tool-use | post-tool-use | stop`.

Antigravity runs a hook command with a JSON payload on stdin and reads a JSON decision from
stdout. The contract is documented in Antigravity's own docs (shipped inside the product:
``builtin/skills/agy-customizations/docs/hooks.md``): camelCase protojson, the tool call under ``toolCall``
(``{"name": ..., "args": {...}}``) and ``workspacePaths`` for the workspace. agflow also accepts the older flat
variants it used to guess (``tool_name``/``tool_input``, ...); the key names live in ``platform_vocab``. A payload
agflow does not understand never breaks the agent (the hook returns ``{}``) but is never silent either: it is
recorded for ``agflow doctor`` and the dashboard (see ``hook_health``), with ``payload_problems`` as the check.

* PreToolUse  -> {"decision": "deny"|"ask", "reason": ...} for dangerous actions, else {} (no opinion;
                 we never return "allow", which could bypass the user's own permission settings).
* PostToolUse -> {} ; appends a compact entry to the ledger of every active agflow run.
* Stop        -> {"decision": "continue", "reason": ...} while an active run still has claimable
                 nodes and is making progress; otherwise {}.

Three promises make these hooks safe to leave on in every Antigravity session (a failing hook is an error to the
agent: "pre-tool hooks failed", "Stop hook failed"):

* the answer is always one of the shapes above (``_contract``), whatever a handler returned;
* nothing here can raise or hang: handler crashes are counted (``hook_health``), and a watchdog answers ``{}``
  before Antigravity's own hook timeout would turn a slow hook into an error;
* a session with no active run pays almost nothing: the idle fast path answers from one directory listing and one
  small JSON file, without loading the run store, pydantic, jinja or YAML.

This module is imported on every hook call, so its imports are deliberately stdlib-light (``urllib.request``,
``traceback`` and ``dataclasses`` alone cost more than the whole rest); ``tests/test_hook_speed.py`` pins that.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any, NamedTuple

from agflow import paths, platform_vocab

MAX_STOP_CONTINUES_WITHOUT_PROGRESS = 2
# A run nobody has touched for this long is not being orchestrated by anyone any more. Stop must not drag a *new*
# conversation back into it, and the ledger must not keep filling with unrelated tool calls.
STALE_RUN_SECONDS = 24 * 3600
# Antigravity kills a hook at its `timeout` (10 s in the installed hooks.json; 30 s when unset) and reports an error.
# Answering "no opinion" ourselves a little earlier turns a stuck lock into a missing ledger line instead.
HOOK_BUDGET_SECONDS = 8.0
PROBE_ENV = "AGFLOW_HOOK_PROBE"


def _keys(group: str) -> tuple[str, ...]:
    """The payload key variants for one role (tool name, arguments, command, path, workspace) from the vocabulary."""
    return platform_vocab.current().keys(group)


class GuardRule(NamedTuple):
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


def _containers(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The payload itself, then the object(s) that hold the tool call (``toolCall`` in the documented contract)."""
    found = [payload]
    for key in _keys("tool_call"):
        if isinstance(payload.get(key), dict):
            found.append(payload[key])
    return found


def tool_name(payload: dict[str, Any]) -> str:
    for container in _containers(payload):
        name = _first(container, _keys("tool_name"))
        if isinstance(name, dict):
            name = _first(name, ("name",))
        if name:
            return str(name)
    return ""


def tool_args(payload: dict[str, Any]) -> dict[str, Any]:
    for container in _containers(payload):
        args = _first(container, _keys("tool_args"))
        if args is None:
            continue
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"command": args}
        return args if isinstance(args, dict) else {}
    return {}


def file_uri_to_path(uri: str) -> str:
    """``file:///C:/x`` -> ``C:/x`` (Windows) or ``/x`` (POSIX); ``file://server/share/x`` -> ``//server/share/x``.

    ``urllib.request.url2pathname`` does this too, but importing ``urllib.request`` costs ~100 ms per hook call."""
    from urllib.parse import unquote, urlparse

    parsed = urlparse(uri)
    path = unquote(parsed.path)
    if os.name == "nt":
        if parsed.netloc and parsed.netloc.lower() != "localhost":
            return f"//{parsed.netloc}{path}"
        if re.match(r"/[A-Za-z]:", path):
            path = path[1:]
    return path


def payload_workspace(payload: dict[str, Any]) -> Path:
    ws = _first(payload, _keys("workspace"))
    if isinstance(ws, list) and ws:
        ws = ws[0]
    if isinstance(ws, str) and ws.startswith("file://"):
        ws = file_uri_to_path(ws)
    return paths.find_workspace(Path(ws) if ws else None)


# --- guard ------------------------------------------------------------------------------------


def load_guard_overrides(workspace: Path) -> tuple[list[GuardRule], list[re.Pattern[str]]]:
    """Optional <ws>/.agents/agflow/guard.yaml: {deny: [regex], ask: [regex], allow: [regex]}."""
    guard_file = paths.workspace_state_dir(workspace) / "guard.yaml"
    if not guard_file.is_file():
        return [], []  # the common case: no YAML parser (ruamel, ~80 ms) needed to find out there is nothing to read
    from agflow.fsutil import read_yaml

    data = read_yaml(guard_file, {}) or {}
    if not isinstance(data, dict):
        raise ValueError(f"guard.yaml must be a mapping of deny/ask/allow lists, not {type(data).__name__}")
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
    if not _guard_enabled():
        return {}
    args = tool_args(payload)
    try:
        extra, allow = load_guard_overrides(payload_workspace(payload))
    except Exception as exc:  # noqa: BLE001 - a broken guard.yaml must not switch the built-in rules off with it
        extra, allow = [], []
        _record_problem("pre-tool-use", "guard_yaml_invalid", f"{type(exc).__name__}")
    command = _command_text(args)
    if command:
        decision = check_command(command, extra, allow)
        if decision:
            return decision
    target = _first(args, _keys("path"))
    if isinstance(target, str) and target:
        return check_path(target)
    return {}


# --- is the payload one we understand? -------------------------------------------------------------------------------

TOOL_EVENTS = ("pre-tool-use", "post-tool-use")


def payload_key_names(payload: Any, prefix: str = "", depth: int = 3) -> list[str]:
    """Key *names* only (``toolCall.args.CommandLine``), never values: safe to store and to show."""
    names: list[str] = []
    if isinstance(payload, dict) and depth > 0:
        for key, value in payload.items():
            path = f"{prefix}{key}"
            names.append(path)
            names += payload_key_names(value, path + ".", depth - 1)
    return names[:40]


def payload_problems(event: str, payload: dict[str, Any]) -> list[str]:
    """Why the guard or the ledger would not see this call, or ``[]`` when the shape is understood.

    * ``empty_payload``: a tool event with nothing in it;
    * ``unknown_shape``: no tool name and no arguments under any key agflow knows (PostToolUse also accepts
      the documented bookkeeping fields, since the documented PostToolUse payload carries no tool call);
    * ``guard_blind:command`` / ``guard_blind:path``: the tool is a known command / file tool but the command or
      path could not be found in its arguments, so the guard would have nothing to check.
    """
    if event not in TOOL_EVENTS:
        return []
    if not payload:
        return ["empty_payload"]
    vocab = platform_vocab.current()
    name = tool_name(payload)
    has_args = any(_first(c, vocab.keys("tool_args")) is not None for c in _containers(payload))
    if not name and not has_args:
        if event == "post-tool-use" and any(k in payload for k in vocab.keys("common")):
            return []
        return ["unknown_shape"]
    if event == "pre-tool-use":
        args = tool_args(payload)
        if name in vocab.command_tools and not _command_text(args):
            return ["guard_blind:command"]
        if name in vocab.file_tools and not isinstance(_first(args, vocab.keys("path")), str):
            return ["guard_blind:path"]
    return []


def _command_text(args: dict[str, Any]) -> str:
    command = _first(args, _keys("command"))
    if isinstance(command, list):
        command = " ".join(map(str, command))
    return command.strip() if isinstance(command, str) else ""


def _alarm(event: str, payload: dict[str, Any], problems: list[str]) -> None:
    """Leave evidence that the guard could not do its job. Never raises: the agent comes first."""
    from agflow import hook_health

    keys = payload_key_names(payload)
    for problem in problems:
        try:
            hook_health.record(problem, event, keys)
        except OSError as exc:
            _debug_log(event, {"alarm": problem, "health_record_failed": repr(exc)})
    _debug_log(event, {"alarm": problems, "keys": keys})  # unconditional: no need to know about AGFLOW_HOOK_DEBUG
    try:
        _note_in_ledgers(event, payload, problems, keys)
    except (OSError, ValueError) as exc:
        _debug_log(event, {"alarm": problems, "ledger_note_failed": repr(exc)})


def _note_in_ledgers(event: str, payload: dict[str, Any], problems: list[str], keys: list[str]) -> None:
    """Tell every active run (once per reason, not on every call) so the dashboard shows it next to the run."""
    from agflow.graph.store import RunStore

    store = RunStore(payload_workspace(payload))
    for run in store.active():
        recent = store.read_ledger(run.run_id, limit=50)
        for reason in problems:
            if not any(e.get("event") == "guard_unavailable" and e.get("reason") == reason for e in recent):
                store.ledger(run.run_id, {"event": "guard_unavailable", "hook": event, "reason": reason, "keys": keys})


# --- ledger / stop ------------------------------------------------------------------------------


def _no_active_run(workspace: Path) -> bool:
    """True only when the run-status index *proves* that nothing is running (the idle fast path).

    One directory listing and one small JSON file, stdlib only. Any doubt -- no index yet, a run directory the index
    has not seen, a corrupt entry, an unreadable file -- answers False and the caller takes the full ``RunStore``
    path, which reconciles the index and stays the authority. Wrongly answering "idle" would drop ledger lines and
    Stop continuations, so every branch that is not a proof is a False.
    """
    runs_dir = paths.runs_dir(workspace.resolve())
    try:
        with os.scandir(runs_dir) as it:
            listed = {e.name for e in it if not e.name.startswith(".") and e.is_dir()}
    except FileNotFoundError:
        return True  # no run was ever started here
    except OSError:
        return False
    if not listed:
        return True
    try:
        with open(runs_dir / paths.RUN_INDEX_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    entries = data.get("runs") if isinstance(data, dict) and data.get("version") == paths.RUN_INDEX_VERSION else None
    if not isinstance(entries, dict):
        return False
    for name in listed:
        known = entries.get(name)
        if not isinstance(known, dict) or known.get("corrupt") or known.get("status") == "running":
            return False
    return True


def _live_runs(store: Any) -> list[Any]:
    """The running runs somebody is still working on (see ``STALE_RUN_SECONDS``)."""
    now = time.time()
    return [run for run in store.active() if now - run.updated_at <= STALE_RUN_SECONDS]


def post_tool_use(payload: dict[str, Any]) -> dict[str, Any]:
    workspace = payload_workspace(payload)
    if _no_active_run(workspace):
        return {}
    from agflow.graph.store import RunStore

    try:
        store = RunStore(workspace)
        active = _live_runs(store)
    except Exception as exc:  # noqa: BLE001 - the agent comes first, but an unreadable run store is counted
        _record_problem("post-tool-use", "store_unreadable", type(exc).__name__)
        return {}
    if not active:
        return {}
    args = tool_args(payload)
    brief = _first(args, _keys("command")) or _first(args, _keys("path")) or ""
    entry = {"event": "tool", "tool": tool_name(payload), "detail": str(brief)[:300]}
    for run in active:
        try:
            store.ledger(run.run_id, entry)
        except OSError:
            continue
    return {}


# A termination reason containing one of these is not "the model finished a turn": something went wrong, a limit was
# hit, or the agent is waiting for a person. Continuing then is wrong. Only the documented reason for a normal stop
# (``model_stop``) has none of them, and a reason agflow has never seen is judged by its words, not by its absence.
STOP_FINAL_WORDS = ("error", "fail", "exceed", "limit", "quota", "timeout", "cancel", "interrupt", "abort", "pause",
                    "user", "wait", "input")


def stop_is_final(payload: dict[str, Any]) -> bool:
    """The agent stopped because something went wrong or a limit was hit: pushing it on would only repeat that.

    The documented Stop payload carries ``error`` (present when it stopped due to an error) and ``terminationReason``
    (``model_stop``, ``max_steps_exceeded``, ``error``, ...). A network failure ends a turn as an error; asking the
    agent to "continue" then re-enters the loop against the same outage and shows up as a second failure."""
    if payload.get("error"):
        return True
    reason = str(payload.get("terminationReason") or payload.get("termination_reason") or "").lower()
    return any(word in reason for word in STOP_FINAL_WORDS)


def stop(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep the orchestrator going while an active run has claimable work and is making progress."""
    if stop_is_final(payload):
        return {}
    workspace = payload_workspace(payload)
    if _no_active_run(workspace):
        return {}
    from agflow.graph import engine
    from agflow.graph.store import RunStore

    try:
        store = RunStore(workspace)
        active = _live_runs(store)
    except Exception as exc:  # noqa: BLE001
        _record_problem("stop", "store_unreadable", type(exc).__name__)
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


def _guard_enabled() -> bool:
    return os.environ.get("AGFLOW_GUARD", "on").lower() not in ("off", "0", "false")


# --- the answer -----------------------------------------------------------------------------------------------------


def contract(event: str, result: Any) -> dict[str, Any]:
    """Squeeze a handler's answer into exactly the shapes Antigravity documents for this event (hooks.md).

    PreToolUse: ``{}`` (no opinion) or ``{"decision": "deny"|"ask", "reason": str}`` -- never ``allow``, never an
    unknown decision (an unrecognised one blocks the tool call "for safety"). PostToolUse: ``{}``. Stop: ``{}`` or
    ``{"decision": "continue", "reason": <non-empty>}`` (a continuation without a reason is ignored anyway)."""
    if not isinstance(result, dict):
        return {}
    reason = result.get("reason")
    if event == "pre-tool-use" and result.get("decision") in ("deny", "ask"):
        return {"decision": result["decision"], "reason": reason if isinstance(reason, str) else ""}
    if event == "stop" and result.get("decision") == "continue" and isinstance(reason, str) and reason.strip():
        return {"decision": "continue", "reason": reason}
    return {}


def read_stdin() -> str:
    """The payload is UTF-8 JSON. ``sys.stdin.read()`` would decode it with the console code page (cp1252 on
    Windows) and raise on bytes that code page leaves undefined -- a command containing Persian or Chinese text is
    enough -- so the bytes are read and decoded here, replacing what is not UTF-8 instead of failing."""
    if sys.stdin is None:
        return ""
    buffer = getattr(sys.stdin, "buffer", None)
    return buffer.read().decode("utf-8", errors="replace") if buffer is not None else sys.stdin.read()


def run_hook(event: str, stdin: str | None = None) -> str:
    raw = read_stdin() if stdin is None else stdin
    problems: list[str] = []
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
        problems.append("payload_not_json")
    else:
        if not isinstance(payload, dict):
            payload = {}
            problems.append("payload_not_json")
        elif event != "pre-tool-use" or _guard_enabled():  # a guard switched off on purpose is not an alarm
            problems += payload_problems(event, payload)
    if problems and (event != "pre-tool-use" or _guard_enabled()):
        _alarm(event, payload, problems)
    if os.environ.get("AGFLOW_HOOK_DEBUG"):
        _debug_log(event, payload)
    try:
        result = HANDLERS[event](payload)
    except Exception as exc:  # a hook must never break the agent -- but a crash is counted, not forgotten
        _debug_log(event, {"error": repr(exc), "payload": payload})
        _record_crash(event, exc)
        result = {}
    return json.dumps(contract(event, result))


def probe(event: str) -> str:
    """What ``agflow doctor`` asks of an installed hook: load everything a real call can load, touch nothing.

    Run with ``AGFLOW_HOOK_PROBE=1``. Unlike a real call it does not swallow errors -- the point is to find out
    whether the install can run a hook at all (a broken tool environment fails here, on the import)."""
    import importlib

    for module in ("agflow.hook_health", "agflow.fsutil", "agflow.graph.engine", "agflow.graph.store"):
        importlib.import_module(module)
    if event not in HANDLERS:
        raise ValueError(f"unknown hook event {event!r}")
    return "{}"


# --- the process ----------------------------------------------------------------------------------------------------


def _answer(text: str) -> None:
    """Write the one JSON line Antigravity reads."""
    try:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    except (OSError, ValueError):
        pass  # the reader went away; nothing useful is left to do


def _arm_watchdog(seconds: float) -> tuple[Any, Any]:
    """After ``seconds`` answer ``{}`` and exit, unless the handler got to answer first.

    Returns ``(lock, timer)``: whoever takes the lock owns the answer. The lock belongs to this call (never a module
    global), so a timer left over from an earlier in-process call can only ever answer for that earlier call."""
    import threading

    lock = threading.Lock()

    def expire() -> None:
        if lock.acquire(blocking=False):  # the handler has not started answering
            _answer("{}")
            os._exit(0)

    timer = threading.Timer(seconds, expire)
    timer.daemon = True
    timer.start()
    return lock, timer


def main(event: str, *, budget: float = HOOK_BUDGET_SECONDS) -> int:
    """Process entry of ``agflow hook <event>``: exit 0 with exactly one JSON object on stdout, whatever happens.

    Failure shows up as a missing ledger line or an unprotected tool call -- both counted for ``agflow doctor`` --
    never as an error inside the agent. The exception is the doctor's own probe (``AGFLOW_HOOK_PROBE=1``), which
    must fail loudly: exit 1 with the traceback on stderr."""
    if os.environ.get(PROBE_ENV):
        try:
            _answer(probe(event))
        except Exception:
            import traceback

            traceback.print_exc()
            return 1
        return 0
    lock, timer = _arm_watchdog(budget)
    try:
        out = run_hook(event)
    except Exception as exc:  # noqa: BLE001 - nothing may escape: this is the last line of defence
        sys.stderr.write(f"agflow hook {event}: {type(exc).__name__} (answered {{}}; run `agflow doctor`)\n")
        out = "{}"
    if lock.acquire(blocking=False):  # False: the watchdog is answering right now and will exit the process
        timer.cancel()
        _answer(out)
    return 0


def _record_problem(event: str, kind: str, detail: str = "") -> None:
    """Count something that made a hook less useful than it should be (shown by doctor / the dashboard)."""
    from agflow import hook_health

    try:
        hook_health.record(kind, event, [], detail=detail)
    except OSError as err:
        _debug_log(event, {"problem": kind, "not_recorded": repr(err)})


def _record_crash(event: str, exc: Exception) -> None:
    """Count a handler crash for doctor/dashboard: the exception type and where it happened, never its message."""
    tb = exc.__traceback__
    while tb is not None and tb.tb_next is not None:
        tb = tb.tb_next
    where = f" at {Path(tb.tb_frame.f_code.co_filename).name}:{tb.tb_lineno}" if tb is not None else ""
    _record_problem(event, "handler_error", f"{type(exc).__name__}{where}")


def _debug_log(event: str, payload: dict[str, Any]) -> None:
    try:
        f = paths.global_state_dir() / "hook-debug.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time(), "event": event, "payload": payload}, default=str)[:20000] + "\n")
    except OSError:
        pass
