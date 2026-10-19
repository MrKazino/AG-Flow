"""Does an installed agflow hook command actually run?  (`agflow doctor`, `agflow fix`)

A hook that cannot start is invisible by design: the fail-open wrapper (``hookfile.fail_open``) answers ``{}`` so the
agent keeps working. That is the right thing for the agent and the wrong thing for the person who believes the guard
and the run ledger are on. This module closes the gap: it runs each installed agflow hook command the way Antigravity
does -- ``cmd /c <line>`` or ``sh -c <line>`` -- with ``AGFLOW_HOOK_PROBE=1`` (the hook then loads everything a real
call loads, touches no run, and exits non-zero on any error), and reports what it finds:

* the wrapper's note on stderr (``agflow hook failed``): the executable was missing, or its environment was broken;
* a non-zero exit, a timeout, or an answer that is not a JSON object.

Running a command that a file names is only safe when the file cannot be somebody else's, so three limits apply and
each has a test (``tests/test_hooks_doctor.py``):

* only the **global** copy of the agflow plugin (under the user's own ``~/.gemini``) is probed -- never a file that
  sits in a workspace, which may belong to a repository that was just cloned;
* only a line **exactly as ``agflow install`` writes it** is run (``hookfile.installer_shaped``): one agflow call and
  the fixed fail-open tail, with no room for a second command or a variable. Anything else is left alone;
* the working directory is an empty scratch folder, so a program planted beside ``hooks.json`` is never picked up by
  a bare ``agflow`` (``cmd`` looks in the current directory first).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from agflow import hookfile, paths, platform_vocab
from agflow.fsutil import read_json
from agflow.hooks import PROBE_ENV
from agflow.inventory import Inventory
from agflow.models import Issue, Kind, Level

TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    detail: str = ""


def _last_error_line(stderr: str) -> str:
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip() and hookfile.FAILOPEN_MARKER not in ln]
    return (lines[-1] if lines else "")[:300]


def run_probe(command: str, *, timeout: float = TIMEOUT_SECONDS) -> ProbeResult:
    """Run one installer-shaped hook command line like the hook runner does and judge the outcome.

    Refuses (``ok=False``, nothing executed) a line that is not installer-shaped: callers filter first, this is the
    second lock on the same door."""
    if not hookfile.installer_shaped(command):
        return ProbeResult(False, "was not run: the command is not in the form `agflow install` writes")
    argv = ["cmd", "/c", command] if os.name == "nt" else ["sh", "-c", command]
    env = {**os.environ, PROBE_ENV: "1"}
    with tempfile.TemporaryDirectory(prefix="agflow-probe-") as scratch:
        # the payload is shaped like a real one, so an agflow that predates the probe switch handles it as a harmless
        # call in an empty workspace instead of recording it as a strange payload
        payload = json.dumps({"toolCall": {"name": "agflow-probe", "args": {}}, "workspacePaths": [scratch]})
        try:
            proc = subprocess.run(argv, input=payload.encode("utf-8"), capture_output=True, cwd=scratch, env=env,
                                  timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return ProbeResult(False, f"did not answer within {timeout:g} seconds")
        except OSError as exc:
            return ProbeResult(False, f"could not be started: {exc}")
    stderr = proc.stderr.decode("utf-8", errors="replace")
    if hookfile.FAILOPEN_MARKER in stderr:
        reason = _last_error_line(stderr)
        return ProbeResult(False, "failed and fell back to answering {}" + (f" ({reason})" if reason else ""))
    if proc.returncode != 0:
        reason = _last_error_line(stderr)
        return ProbeResult(False, f"exited with code {proc.returncode}" + (f" ({reason})" if reason else ""))
    try:
        answer = json.loads(proc.stdout.decode("utf-8", errors="replace").strip() or "null")
    except ValueError:
        answer = None
    if not isinstance(answer, dict):
        return ProbeResult(False, "did not answer with a JSON object")
    return ProbeResult(True)


def _in_global_plugin(path: Path) -> bool:
    """Is this hooks.json inside the user's own agflow plugin folder (not a workspace, not another plugin)?"""
    try:
        base = paths.global_layout().plugins_dir / paths.PLUGIN_NAME  # type: ignore[operator]
        path.resolve().relative_to(base.resolve())
    except (ValueError, OSError, TypeError):
        return False
    return True


def probe_issues(inv: Inventory, *, vocab: platform_vocab.Vocab | None = None) -> list[Issue]:
    """One ``hooks.broken`` error per installed agflow hook command that cannot run. The commands are independent
    processes, so they run side by side (three start-ups in the time of one); the result order is unchanged."""
    from concurrent.futures import ThreadPoolExecutor

    todo: list[tuple[Path, str, str]] = []
    done: set[tuple[str, str]] = set()
    for item in inv.of(Kind.HOOK):
        if not item.enabled or item.meta.get("inert") or not item.path.is_file() or not _in_global_plugin(item.path):
            continue
        try:
            data = read_json(item.path, {})
        except (OSError, ValueError):
            continue  # the validator already reports a file that cannot be read
        for ref in hookfile.handlers(data, vocab):
            command = ref.handler.get("command")
            if not (ref.enabled and not ref.inert and hookfile.installer_shaped(command)):
                continue
            if (str(item.path), command) in done:
                continue
            done.add((str(item.path), command))
            todo.append((item.path, ref.event, command))
    if not todo:
        return []
    with ThreadPoolExecutor(max_workers=min(len(todo), 6)) as pool:
        results = list(pool.map(lambda job: run_probe(job[2]), todo))
    return [Issue(Level.ERROR, "hooks.broken",
                  f"the {event} hook {result.detail}. While this lasts agflow's safety guard "
                  "and run ledger are off; the agent itself is not affected (the hook fails open). "
                  "`agflow fix` (or `agflow install`) points the hooks at a working agflow; if "
                  "agflow was installed with `uv tool`, `uv tool install --force --reinstall "
                  "<agflow checkout or package>` repairs the broken environment", path)
            for (path, event, _), result in zip(todo, results, strict=True) if not result.ok]
