"""``hooks.json``: Antigravity's documented layout, the older layout agflow used to write, and the command line.

Documented layout (``agy-customizations/docs/hooks.md``, shipped inside Antigravity): a JSON object of *named hooks*.
Each hook maps event names to handlers (plus an optional ``"enabled": false``). The tool events hold *groups*
(``{"matcher": <regex on the tool name>, "hooks": [handler, ...]}``); the other events hold handlers directly. A
handler is ``{"type": "command", "command": <shell line>, "timeout": <seconds>}``::

    {"agflow": {"PreToolUse": [{"matcher": "run_command", "hooks": [{"type": "command", "command": "..."}]}],
                "Stop": [{"type": "command", "command": "..."}]}}

Before agflow 0.3 this tool wrote ``{"hooks": {"PreToolUse": [{"matcher": ..., "command": ...}]}}``. Antigravity reads
that as a hook *named* "hooks" whose tool-event entries are not groups, and ignores them: the guard and the run
ledger never ran, and only the flat ``Stop`` entry was honoured. :func:`handlers` reads both layouts and flags the
entries Antigravity would ignore (``inert``), so ``agflow doctor`` can say so.

Which events are grouped comes from the vocabulary (``hook_events_grouped``), not from this module.

Stdlib only.
"""

from __future__ import annotations

import os
import re
from typing import Any, NamedTuple

from agflow import platform_vocab

# Written to stderr by the fail-open wrapper when the real command could not run; `agflow doctor` looks for it.
FAILOPEN_MARKER = "agflow hook failed"
_FAILOPEN_NOTE = FAILOPEN_MARKER + " - continuing without it"
# What an agflow hook command looks like once the installer has rewritten it (bare, quoted, `python -m agflow`, .exe).
AGFLOW_HOOK_COMMAND = re.compile(r"""\bagflow(?:\.exe)?["']?\s+hook\s+[a-z-]+""", re.IGNORECASE)


class HandlerRef(NamedTuple):
    hook: str  # the hook's name, e.g. "agflow" (or "hooks" for the pre-0.3 layout)
    event: str
    where: str  # "PreToolUse[0].hooks[0]" -- for messages
    number: int  # running number within (hook, event): stable item names
    matcher: Any  # the group's matcher (None for a plain handler or a group without one)
    handler: dict[str, Any]  # the handler object itself (mutable: the installer rewrites ``command`` in place)
    enabled: bool  # the named hook is not switched off with "enabled": false
    inert: bool  # Antigravity would ignore this entry: a plain handler under a grouped event, or a group elsewhere


def handlers(data: Any, vocab: platform_vocab.Vocab | None = None) -> list[HandlerRef]:
    """Every handler object in a parsed ``hooks.json``. Tolerant: whatever is not recognisable is skipped (the
    validator reports it), and nothing raises."""
    vocab = vocab or platform_vocab.current()
    out: list[HandlerRef] = []
    if not isinstance(data, dict):
        return out
    for hook, spec in data.items():
        if not isinstance(spec, dict):
            continue
        enabled = spec.get("enabled", True) is not False
        for event, entries in spec.items():
            if event == "enabled" or not isinstance(entries, list):
                continue
            grouped = event in vocab.hook_events_grouped
            known = event in vocab.hook_events
            number = 0
            for i, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue
                members = entry.get("hooks")
                if isinstance(members, list):  # a group: {matcher, hooks: [...]}
                    for j, handler in enumerate(members):
                        if isinstance(handler, dict):
                            out.append(HandlerRef(hook, event, f"{event}[{i}].hooks[{j}]", number, entry.get("matcher"),
                                                  handler, enabled, inert=known and not grouped))
                            number += 1
                else:  # a plain handler
                    out.append(HandlerRef(hook, event, f"{event}[{i}]", number, entry.get("matcher"), entry, enabled,
                                          inert=known and grouped))
                    number += 1
    return out


def is_agflow_command(command: Any) -> bool:
    """Does this command line mention agflow's hook entry point (anywhere in it)? A loose test, for reporting only:
    never a reason to execute the line -- that needs :func:`installer_shaped`."""
    return isinstance(command, str) and bool(AGFLOW_HOOK_COMMAND.search(command))


# One command word as `agflow install` writes it: unquoted (no space, no shell syntax), double-quoted (Windows) or
# single-quoted (POSIX, `shlex.quote`). Nothing in any of the three can start a second command, expand a variable or
# (no `=` in the unquoted form) read as an environment assignment in front of the command.
_WORD = r"""(?:[\w.:+@~/\\-]+|"[\w.:+=@~/\\ ()-]+"|'[\w.:+=@~/\\ ()%,-]+')"""
_HEAD = rf"""{_WORD}(?: -m agflow)? hook (?:pre-tool-use|post-tool-use|stop)"""


def _tail(windows: bool) -> str:
    return fail_open("", windows=windows).removeprefix(" || ")


def installer_shaped(command: Any, *, windows: bool | None = None) -> bool:
    """True only for a hook command line exactly as `agflow install` writes it:
    ``<executable> [-m agflow] hook <event>``, optionally followed by the fixed fail-open tail -- and nothing else.

    `agflow doctor` runs the commands it finds, to prove that they start. A hooks.json can come from a repository the
    user has just cloned, so a line is executed only when it *cannot* contain anything but one agflow call: no `;`,
    `&&`, `|`, `$(...)`, backtick, redirection, newline or variable (`%x%`, `$x`) outside the fixed tail."""
    windows = os.name == "nt" if windows is None else windows
    if not isinstance(command, str):
        return False
    return re.fullmatch(rf"{_HEAD}(?: \|\| {re.escape(_tail(windows))})?", command) is not None


def fail_open(command: str, *, windows: bool | None = None) -> str:
    """``command`` wrapped so that it can never fail the agent.

    Antigravity treats a hook that exits non-zero as an error ("Stop hook failed", "pre-tool hooks failed"), which
    ends the agent's turn. A broken agflow install -- the tool environment was deleted, an upgrade replaced it, the
    executable is missing -- must cost the guard and the ledger, not the user's session. So when ``command`` fails,
    for any reason (not found, crashed, quoting that the shell cannot parse), the wrapper tells stderr and answers
    ``{}`` -- "no opinion" for every event -- with exit code 0. ``agflow doctor`` finds the note and reports it.

    Verified by running real lines through ``cmd /c`` and ``sh -c`` (``tests/test_hookfile.py``)."""
    windows = os.name == "nt" if windows is None else windows
    if windows:
        return f"{command} || (echo {_FAILOPEN_NOTE} 1>&2 & echo {{}})"
    return f"{command} || {{ echo '{_FAILOPEN_NOTE}' >&2; echo '{{}}'; }}"
