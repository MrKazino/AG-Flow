"""agflow easy installer — the cross-platform logic behind easy-installer.bat.

Launched by easy-installer.bat (Windows batch + POSIX sh polyglot) through
`uv run --no-project --python ">=3.10" scripts/easy_install.py [options]`, but can also be run
directly with any Python >= 3.10 when `uv` is on PATH. Standard library only.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STEPS = 7


class InstallFailed(RuntimeError):
    pass


# --- output helpers ------------------------------------------------------------------------------

def _color(code: str, text: str) -> str:
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def step(n: int, title: str) -> None:
    print(f"\n{_color('1;36', f'[{n}/{STEPS}]')} {_color('1', title)}", flush=True)


def info(msg: str) -> None:
    print(f"      {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"      {_color('33', 'warning:')} {msg}", flush=True)


def ok(msg: str) -> None:
    print(f"      {_color('32', 'ok')} {msg}", flush=True)


# --- helpers -----------------------------------------------------------------------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="easy-installer",
        description="Install requirements, build agflow from source and install it (plus the Antigravity plugin).",
    )
    p.add_argument("-y", "--yes", action="store_true", help="never ask questions (non-interactive)")
    p.add_argument("--test", action="store_true", help="also run the test suite and plugin validation")
    p.add_argument("--no-sdk", action="store_true", help="skip the Antigravity SDK extra (headless runs)")
    p.add_argument("--no-plugin", action="store_true", help="only install the CLI, not the Antigravity plugin")
    p.add_argument("--profile", metavar="NAME",
                   help="install only a plugin profile: core, secure, frontend, devops, full")
    p.add_argument("--editable", action="store_true", help="developer install: code changes apply without reinstalling")
    p.add_argument("--uninstall", action="store_true", help="remove the plugin and the agflow tool")
    return p.parse_args(argv)


def project_version(root: Path) -> str:
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    if not re.search(r'^name\s*=\s*"agflow"', text, re.MULTILINE):
        raise InstallFailed(f"{root} is not the agflow project (pyproject.toml name != agflow)")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        raise InstallFailed("could not read the version from pyproject.toml")
    return m.group(1)


def nested_copies(root: Path) -> list[Path]:
    """Detect the common unzip mistake: a newer copy extracted into a sub-folder of an old copy."""
    return sorted(d for d in root.iterdir()
                  if d.is_dir() and (d / "easy-installer.bat").is_file() and (d / "pyproject.toml").is_file())


def find_wheel(dist: Path, version: str) -> Path:
    wheels = sorted(dist.glob(f"agflow-{version}-*.whl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not wheels:
        raise InstallFailed(f"no wheel for agflow {version} in {dist}")
    return wheels[0]


def requirement(target: Path, sdk: bool) -> str:
    """PEP 508 direct reference, e.g. `agflow[sdk] @ file:///.../agflow-0.1.0-py3-none-any.whl`."""
    return f"agflow{'[sdk]' if sdk else ''} @ {target.resolve().as_uri()}"


def uv_path() -> str:
    uv = os.environ.get("AGFLOW_UV") or shutil.which("uv")
    if not uv:
        raise InstallFailed("uv not found. Run easy-installer.bat (it installs uv) or see https://docs.astral.sh/uv/")
    return uv


def run(cmd: list[str], *, check: bool = True, capture: bool = False, cwd: Path = ROOT) -> subprocess.CompletedProcess:
    info("$ " + " ".join(str(c) for c in cmd))
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}  # child Pythons never crash on non-UTF-8 consoles/pipes
    proc = subprocess.run([str(c) for c in cmd], cwd=cwd, text=True, env=env,
                          capture_output=capture, encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-1500:] if capture else ""
        raise InstallFailed(f"command failed ({proc.returncode}): {' '.join(map(str, cmd))}\n{detail}")
    return proc


def run_stream(cmd: list[str], *, cwd: Path = ROOT) -> tuple[int, str]:
    """Run a command, echo its output live and also return it (so failures can be classified)."""
    info("$ " + " ".join(str(c) for c in cmd))
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen([str(c) for c in cmd], cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    lines = []
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        lines.append(line)
    return proc.wait(), "".join(lines)


_LOCK_MARKERS = ("os error 5)", "os error 32)", "access is denied", "being used by another process")
_SDK_MARKERS = ("no solution found", "no wheels with a matching platform", "has no wheels", "unsatisfiable",
                "no matching distribution", "not found in the package registry")


def classify_failure(output: str) -> str:
    """'locked' (Windows: files of the installed agflow are in use), 'sdk' (the Antigravity SDK has no
    wheel for this platform/Python) or 'other'."""
    low = output.lower()
    if any(m in low for m in _LOCK_MARKERS):
        return "locked"
    if "google-antigravity" in low and any(m in low for m in _SDK_MARKERS):
        return "sdk"
    return "other"


# --- processes that keep the installed agflow busy (Windows only) -----------------------------------------

# Match on the executable's path only. The old query also matched any process whose *command line* mentioned the
# folder, which stopped editors and terminals that merely had it open. CreationDate is recorded so the PID can be
# re-verified right before it is stopped (Windows reuses PIDs).
_PS_FIND = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'
$t = $env:AGFLOW_TOOL_ENV.TrimEnd('\') + '\'
$b = $env:AGFLOW_TOOL_EXE
$cmp = [System.StringComparison]::OrdinalIgnoreCase
$items = @(Get-CimInstance Win32_Process | Where-Object {
    $_.ProcessId -ne $PID -and $_.ExecutablePath -and
      ($_.ExecutablePath.StartsWith($t, $cmp) -or $_.ExecutablePath -ieq $b)
  } | ForEach-Object {
    $made = if ($_.CreationDate) { $_.CreationDate.ToString('o') } else { '' }
    [pscustomobject]@{ pid = $_.ProcessId; ppid = $_.ParentProcessId; name = $_.Name;
                       path = $_.ExecutablePath; cmd = $_.CommandLine; created = $made } })
ConvertTo-Json -InputObject $items -Compress -Depth 3
"""

_PS_IDENTITY = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'
$ids = @($env:AGFLOW_PIDS -split ',' | Where-Object { $_ })
$items = @(Get-CimInstance Win32_Process | Where-Object { $ids -contains [string]$_.ProcessId } |
  ForEach-Object {
    $made = if ($_.CreationDate) { $_.CreationDate.ToString('o') } else { '' }
    [pscustomobject]@{ pid = $_.ProcessId; path = $_.ExecutablePath; created = $made } })
ConvertTo-Json -InputObject $items -Compress -Depth 3
"""


def tool_paths(uv: str) -> tuple[Path, Path]:
    """(uv's environment for the agflow tool, the agflow launcher uv puts on PATH)."""
    tools = subprocess.run([uv, "tool", "dir"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    bindir = subprocess.run([uv, "tool", "dir", "--bin"], capture_output=True, text=True, encoding="utf-8",
                            errors="replace")
    tool_env = Path(tools.stdout.strip().splitlines()[-1]) / "agflow" if tools.stdout.strip() else Path()
    exe = Path(bindir.stdout.strip().splitlines()[-1]) / "agflow.exe" if bindir.stdout.strip() else Path()
    return tool_env, exe


def parse_processes(raw: str) -> list[dict]:
    raw = (raw or "").strip()
    if not raw:
        return []
    data = json.loads(raw)
    if isinstance(data, dict):
        data = [data]
    return [p for p in data if isinstance(p, dict) and p.get("pid")]


def describe_process(p: dict) -> str:
    cmd = (p.get("cmd") or "").lower()
    if "mcp" in cmd and "serve" in cmd:
        role = "agflow MCP server (started by Google Antigravity or the agy CLI)"
    elif "dashboard" in cmd:
        role = "agflow dashboard"
    elif " hook " in f"{cmd} ":
        role = "agflow hook"
    else:
        role = "agflow"
    return f"PID {p['pid']:<6} {p.get('name') or '?':<12} {role}"


def find_agflow_processes(uv: str) -> list[dict]:
    """Processes running from the installed agflow. Windows can't replace files that are in use, so these
    block `uv tool install --reinstall`. Returns [] on other platforms (they allow it)."""
    if os.name != "nt":
        return []
    tool_env, exe = tool_paths(uv)
    if not tool_env.name or not tool_env.exists():
        return []
    env = {**os.environ, "AGFLOW_TOOL_ENV": str(tool_env), "AGFLOW_TOOL_EXE": str(exe)}
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                               "-Command", _PS_FIND], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=env, timeout=60)
        return parse_processes(proc.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        warn("could not list running processes (PowerShell unavailable); continuing")
        return []


def owned_by_tool(proc: dict, tool_env: Path, exe: Path) -> bool:
    """True if the process runs an executable inside agflow's tool environment, or is the agflow launcher itself."""
    path = proc.get("path")
    if not path:
        return False
    norm = os.path.normcase(os.path.normpath(str(path)))
    env = os.path.normcase(os.path.normpath(str(tool_env))) + os.sep  # the separator keeps `agflow-other` out
    return norm.startswith(env) or norm == os.path.normcase(os.path.normpath(str(exe)))


def same_identity(before: dict, now: dict | None) -> bool:
    """The process listed earlier is still the same one: same executable and same start time (PIDs get reused)."""
    if not now or not before.get("created") or before["created"] != now.get("created"):
        return False
    return os.path.normcase(str(before.get("path"))) == os.path.normcase(str(now.get("path")))


def current_identities(pids: list[int]) -> dict[int, dict]:
    """Executable and start time of the given PIDs as they are right now (Windows)."""
    env = {**os.environ, "AGFLOW_PIDS": ",".join(str(p) for p in pids)}
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                               "-Command", _PS_IDENTITY], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=env, timeout=60)
        return {int(p["pid"]): p for p in parse_processes(proc.stdout)}
    except (OSError, subprocess.TimeoutExpired, ValueError):
        warn("could not re-check the processes before stopping them; stopping none of them")
        return {}


def stop_processes(uv: str, procs: list[dict], timeout: float = 10.0) -> None:
    """Stop the listed processes, but only those that are still exactly the processes that were listed."""
    now = current_identities([int(p["pid"]) for p in procs])
    pids = []
    for p in procs:
        pid = int(p["pid"])
        if same_identity(p, now.get(pid)):
            pids.append(pid)
        elif pid in now:
            warn(f"PID {pid} is no longer the process that was listed (the PID was reused); not stopping it")
    pids = sorted(set(pids))
    if not pids:
        return
    args = ["taskkill", "/T", "/F"]
    for pid in pids:
        args += ["/PID", str(pid)]
    subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not {int(p["pid"]) for p in find_agflow_processes(uv)} & set(pids):
            return
        time.sleep(0.5)


def release_agflow(uv: str, assume_yes: bool) -> bool:
    """Make sure nothing runs from the installed agflow. Returns True if processes were stopped."""
    procs = find_agflow_processes(uv)
    if not procs:
        return False
    info("The currently installed agflow is still running, and Windows can't replace files that are in use:")
    for p in procs:
        info(f"  {describe_process(p)}")
    if not confirm("Stop these agflow processes now? (Only agflow's own processes are stopped.)", assume_yes):
        raise InstallFailed("agflow is still running. Close Google Antigravity completely (also check the system "
                            "tray) and any `agflow dashboard` window, then run easy-installer.bat again.")
    stop_processes(uv, procs)
    left = find_agflow_processes(uv)
    if left:
        raise InstallFailed("these agflow processes could not be stopped:\n      "
                            + "\n      ".join(describe_process(p) for p in left)
                            + "\n      Close Google Antigravity completely, then run easy-installer.bat again.")
    ok("stopped the running agflow processes")
    return True


def locked_help(uv: str) -> str:
    tool_env, _ = tool_paths(uv)
    return ("Windows is blocking the update because files of the installed agflow are still in use:\n"
            f"        {tool_env}\n"
            "      Close Google Antigravity completely (also check the system tray), any `agflow dashboard` window,\n"
            "      and any terminal or Explorer window opened inside that folder, then run easy-installer.bat again.\n"
            "      If it still fails, sign out of Windows (or restart) and run it once more.")


def tool_install(uv: str, base: list[str], target: Path, sdk: bool, assume_yes: bool) -> tuple[bool, bool]:
    """`uv tool install` with precise failure handling. Returns (sdk_installed, processes_were_stopped)."""
    stopped = False
    lock_retries = 2
    while True:
        code, output = run_stream([*base, requirement(target, sdk)])
        if code == 0:
            return sdk, stopped
        kind = classify_failure(output)
        if kind == "sdk" and sdk:
            warn("the Antigravity SDK has no package for this platform/Python; installing without it "
                 "(everything works except `agflow graph run --backend sdk`)")
            sdk = False
            continue
        if kind == "locked" and lock_retries > 0:
            lock_retries -= 1
            if release_agflow(uv, assume_yes):  # e.g. Antigravity restarted its agflow MCP server
                stopped = True
            else:
                info("files are still locked (often an antivirus scan); retrying in 3 seconds...")
                time.sleep(3)
            continue
        if kind == "locked":
            raise InstallFailed(locked_help(uv))
        raise InstallFailed("`uv tool install` failed; the reason is in uv's output above")


def confirm(question: str, assume_yes: bool) -> bool:
    """Ask before anything that stops processes or touches ~/.gemini. No terminal means nobody can answer, and an
    unanswered question is not consent: stop and say how to approve explicitly (``--yes``)."""
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        raise InstallFailed(f"{question.strip()} needs your approval, but this session is not interactive. Re-run "
                            "with --yes to approve every step, or run the installer in a terminal.")
    answer = input(f"      {question} [Y/n] ").strip().lower()
    return answer in ("", "y", "yes")


def agflow_exe(uv: str) -> str:
    proc = run([uv, "tool", "dir", "--bin"], capture=True, check=False)
    name = "agflow.exe" if os.name == "nt" else "agflow"
    if proc.returncode == 0 and proc.stdout.strip():
        candidate = Path(proc.stdout.strip().splitlines()[-1]) / name
        if candidate.exists():
            return str(candidate)
    found = shutil.which("agflow")
    if found:
        return found
    raise InstallFailed("agflow was installed but its executable could not be found (check `uv tool dir --bin`)")


def health_check(exe: str) -> None:
    """agflow's own health decides pass/fail; everything else on the machine is summarised separately."""
    if run([exe, "doctor", "--source", "plugin:agflow"], check=False).returncode == 0:
        ok("the agflow plugin is installed and valid")
    else:
        warn("the agflow plugin check reported errors (listed above)")
    report = run([exe, "doctor", "--json"], check=False, capture=True)
    try:
        by_source = json.loads(report.stdout)["by_source"]
    except (ValueError, KeyError, TypeError):
        return
    others = {src: c for src, c in by_source.items() if src != "plugin:agflow" and (c["error"] or c["warning"])}
    if not others:
        return
    errors = sum(c["error"] for c in others.values())
    warnings = sum(c["warning"] for c in others.values())
    info(f"Note: your other skills/plugins/settings have {errors} error(s) and {warnings} warning(s) "
         "that are NOT caused by agflow:")
    for src, c in sorted(others.items(), key=lambda kv: (-kv[1]["error"], -kv[1]["warning"], kv[0])):
        info(f"  - {src}: {c['error']} error(s), {c['warning']} warning(s)")
    info("Review and repair them with `agflow doctor --fix` (every change is previewed and can be undone with "
         "`agflow fix --undo`), or in the dashboard: Doctor -> Fix issues.")


# --- flows -------------------------------------------------------------------------------------------

def install(args: argparse.Namespace) -> None:
    uv = uv_path()

    step(1, "Checking the environment")
    version = project_version(ROOT)
    nested = nested_copies(ROOT)
    if nested:
        raise InstallFailed(
            "another copy of this project is extracted INSIDE this folder:\n      "
            + "\n      ".join(str(p) for p in nested)
            + "\n      You are running the outer (older) copy. Run the easy-installer.bat inside that folder "
            "instead, or move its contents up here, then run this again.")
    uv_version = run([uv, "--version"], capture=True).stdout.strip()
    system = f"{platform.system()} {platform.machine()}"
    ok(f"agflow {version} · {system} · Python {platform.python_version()} · {uv_version}")

    step(2, "Building agflow from source")
    run([uv, "build", "--out-dir", "dist"])
    wheel = find_wheel(ROOT / "dist", version)
    ok(f"built {wheel.name}")

    step(3, "Running the test suite")
    if args.test:
        run([uv, "sync", "--all-extras"])
        run([uv, "run", "pytest", "-q"])
        run([uv, "run", "agflow", "doctor", "--path", "plugin", "--strict"])
        ok("tests and plugin validation passed")
    else:
        info("skipped (add --test to run it)")

    step(4, "Installing the agflow command")
    target = ROOT if args.editable else wheel
    # --refresh-package: never reuse a cached agflow build (versions can repeat while you iterate)
    base = [uv, "tool", "install", "--force", "--reinstall", "--refresh-package", "agflow", "--python", ">=3.10"]
    if args.editable:
        base.append("--editable")
    # Windows can't replace a running program: stop agflow's own processes (e.g. Antigravity's MCP server) first.
    stopped = release_agflow(uv, args.yes)
    sdk, stopped_later = tool_install(uv, base, target, not args.no_sdk, args.yes)
    stopped = stopped or stopped_later
    ok(f"installed agflow {'with' if sdk else 'without'} the Antigravity SDK")

    step(5, "Adding agflow to your PATH")
    shell = run([uv, "tool", "update-shell"], check=False, capture=True)
    shell_msg = (shell.stdout + shell.stderr).strip()
    if shell.returncode == 0:
        info(shell_msg.splitlines()[-1] if shell_msg else "PATH updated")
    elif "up-to-date" in shell_msg or "up to date" in shell_msg:
        info("your shell profile already adds uv's tool directory to PATH (takes effect in new terminals)")
    else:
        warn(shell_msg or "could not update PATH automatically; add `uv tool dir --bin` to your PATH")
    exe = agflow_exe(uv)
    installed = run([exe, "version"], capture=True).stdout.strip()
    info(installed)
    if not installed.endswith(version):
        raise InstallFailed(f"`{exe} version` reports '{installed}' but this folder is agflow {version}: an older "
                            "agflow is being picked up. Run `uv tool uninstall agflow`, then this installer again.")
    ok(f"agflow is at {exe} (open a new terminal if `agflow` is not found yet)")

    step(6, "Installing the Antigravity plugin (master prompt, skills, agents, hooks, orchestrator MCP)")
    if args.no_plugin:
        info("skipped (--no-plugin)")
    elif confirm("Install the agflow plugin into ~/.gemini now? Existing files are backed up.", args.yes):
        cmd = [exe, "install"] + (["--profile", args.profile] if args.profile else [])
        run(cmd)
        health_check(exe)
    else:
        info("skipped — run `agflow install` whenever you are ready")

    step(7, "Done")
    if stopped:
        warn("Restart Google Antigravity now: its agflow MCP server was stopped for the update.")
    print("""
      Next steps:
        1. Restart Google Antigravity (or reload the window) to load the plugin.
        2. In Antigravity, try:  Run the agflow bugfix graph. bug: "<describe a bug>"
        3. Open the manager UI:  agflow dashboard
        4. Try a dry run:        agflow graph run bugfix -i bug=demo --backend fake
        5. Health check:         agflow doctor      (repair: agflow doctor --fix)
      Uninstall everything:      easy-installer.bat --uninstall   (sh easy-installer.bat --uninstall)
""", flush=True)


def uninstall(args: argparse.Namespace) -> None:
    global STEPS
    STEPS = 2
    uv = uv_path()
    step(1, "Removing the Antigravity plugin")
    try:
        exe = agflow_exe(uv)
    except InstallFailed:
        exe = None
    if exe and confirm("Remove the agflow plugin from ~/.gemini and restore backed-up files?", args.yes):
        if run([exe, "uninstall"], check=False).returncode != 0:
            info("no global plugin install found")
    else:
        info("agflow command not found; nothing to remove")
    step(2, "Removing the agflow command")
    release_agflow(uv, args.yes)
    code, output = run_stream([uv, "tool", "uninstall", "agflow"])
    if code == 0:
        ok("agflow removed")
    elif classify_failure(output) == "locked":
        raise InstallFailed(locked_help(uv))
    else:
        info("agflow tool was not installed")


def _safe_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]


def main(argv: list[str] | None = None) -> int:
    _safe_streams()
    args = parse_args(sys.argv[1:] if argv is None else argv)
    print(_color("1", "agflow easy installer") + f"  ({ROOT})")
    try:
        (uninstall if args.uninstall else install)(args)
    except InstallFailed as exc:
        print(f"\n{_color('1;31', 'error:')} {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ncancelled", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
