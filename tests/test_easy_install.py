"""easy-installer.bat (batch + sh polyglot) and its cross-platform helper scripts/easy_install.py."""

import importlib.util
import re
import shutil
import subprocess
import time

import pytest

from tests.conftest import REPO

BAT = REPO / "easy-installer.bat"
spec = importlib.util.spec_from_file_location("easy_install", REPO / "scripts" / "easy_install.py")
easy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(easy)


def test_polyglot_structure():
    raw = BAT.read_bytes()
    assert b"\r" not in raw, "must stay LF-only: CRLF breaks the sh half"
    lines = raw.decode().splitlines()
    assert lines[0] == ':<<"::AGFLOW_SH"'
    end = lines.index("::AGFLOW_SH")
    batch = lines[1:end]
    # cmd.exe label lookups are unreliable in LF files: the batch half must not use labels or goto
    assert not [ln for ln in batch if re.match(r"\s*:[^:]", ln)]
    assert not [ln for ln in batch if re.search(r"\b(goto|call\s+:)", ln, re.I)]
    assert any(ln.startswith("exit /b") for ln in batch)
    assert (REPO / ".gitattributes").read_text().startswith("easy-installer.bat text eol=lf")


@pytest.mark.parametrize("shell", ["sh", "bash", "dash"])
def test_sh_half_parses(shell):
    if not shutil.which(shell):
        pytest.skip(f"{shell} not installed")
    # The script goes in on stdin: a path argument is resolved by the shell's own filesystem view, which on
    # Windows may be a WSL launcher that cannot see a Windows path. The syntax check is identical.
    assert subprocess.run([shell, "-n"], input=BAT.read_bytes()).returncode == 0


def test_help_runs_through_the_polyglot():
    if not shutil.which("uv"):
        pytest.skip("uv not installed")
    proc = subprocess.run(["sh", str(BAT), "--help"], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "--uninstall" in proc.stdout


def test_parse_args():
    a = easy.parse_args(["-y", "--profile", "core", "--no-sdk"])
    assert a.yes and a.profile == "core" and a.no_sdk and not a.test and not a.uninstall


def test_project_version(tmp_path):
    assert easy.project_version(REPO) == re.search(
        r'^version = "(.+)"', (REPO / "pyproject.toml").read_text(), re.M).group(1)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "other"\nversion = "1"\n')
    with pytest.raises(easy.InstallFailed):
        easy.project_version(tmp_path)


def test_find_wheel_and_requirement(tmp_path):
    old = tmp_path / "agflow-0.0.9-py3-none-any.whl"
    new = tmp_path / "agflow-0.1.0-py3-none-any.whl"
    old.write_text("x")
    time.sleep(0.01)
    new.write_text("x")
    assert easy.find_wheel(tmp_path, "0.1.0") == new
    with pytest.raises(easy.InstallFailed):
        easy.find_wheel(tmp_path, "9.9.9")
    req = easy.requirement(new, sdk=True)
    assert req.startswith("agflow[sdk] @ file://") and req.endswith(new.name)
    assert easy.requirement(new, sdk=False).startswith("agflow @ file://")


def test_nested_copy_detection(tmp_path):
    assert easy.nested_copies(tmp_path) == []
    inner = tmp_path / "google-antigravity-workflow"
    inner.mkdir()
    (inner / "easy-installer.bat").write_text("x")
    (inner / "pyproject.toml").write_text("x")
    assert easy.nested_copies(tmp_path) == [inner]
    assert easy.nested_copies(REPO) == []


# --- Windows "Access is denied" during reinstall (real log from a Windows 11 machine) ----------------------

LOCKED_OUTPUT = (
    "Resolved 60 packages in 2.63s\n"
    "error: failed to remove directory `C:\\Users\\Koosha Kazino\\AppData\\Roaming\\uv\\tools\\agflow\\Scripts`: "
    "Access is denied.\n"
    "       (os error 5)\n"
)
SDK_OUTPUT = """  x No solution found when resolving dependencies:
  `-> Because only google-antigravity<=0.1.20 is available and
      google-antigravity==0.1.20 has no wheels with a matching platform tag
      (e.g., `win32`), we can conclude that google-antigravity>=0.1.20 cannot
      be used.
"""


def test_classify_failure():
    assert easy.classify_failure(LOCKED_OUTPUT) == "locked"
    assert easy.classify_failure("error: The process cannot access the file because it is being used by another "
                                 "process. (os error 32)") == "locked"
    assert easy.classify_failure(SDK_OUTPUT) == "sdk"
    assert easy.classify_failure("error: Failed to fetch: https://pypi.org/simple/pydantic/") == "other"
    assert easy.classify_failure("No solution found: requests>=99 is unsatisfiable") == "other"  # not the SDK


def test_parse_and_describe_processes():
    assert easy.parse_processes("") == [] and easy.parse_processes("[]") == []
    one = easy.parse_processes('{"pid": 4242, "name": "agflow.exe", "cmd": "\\"C:\\\\x\\\\agflow.exe\\" mcp serve"}')
    assert one[0]["pid"] == 4242
    assert "MCP server" in easy.describe_process(one[0])
    many = easy.parse_processes('[{"pid": 1, "name": "python.exe", "cmd": "python agflow dashboard --port 8765"},'
                                ' {"pid": 2, "name": "python.exe", "cmd": "agflow hook stop"}, {"name": "no pid"}]')
    assert [p["pid"] for p in many] == [1, 2]
    assert "dashboard" in easy.describe_process(many[0]) and "hook" in easy.describe_process(many[1])


def test_no_process_scan_outside_windows(monkeypatch):
    monkeypatch.setattr(easy.os, "name", "posix")
    assert easy.find_agflow_processes("uv") == []


@pytest.fixture
def fake_install(monkeypatch, tmp_path):
    """Script uv's results and the running-process scan; record what the installer does."""
    state = {"results": [], "procs": [], "stopped": [], "calls": [], "confirm": True}

    def run_stream(cmd, **kw):
        state["calls"].append(cmd[-1])
        return state["results"].pop(0)

    def find(uv):
        return list(state["procs"])

    def stop(uv, procs, timeout=10.0):
        state["stopped"] += [p["pid"] for p in procs]
        state["procs"] = []

    monkeypatch.setattr(easy, "run_stream", run_stream)
    monkeypatch.setattr(easy, "find_agflow_processes", find)
    monkeypatch.setattr(easy, "stop_processes", stop)
    monkeypatch.setattr(easy, "confirm", lambda q, y: state["confirm"])
    monkeypatch.setattr(easy.time, "sleep", lambda s: None)
    monkeypatch.setattr(easy, "tool_paths", lambda uv: (tmp_path / "tools" / "agflow", tmp_path / "agflow.exe"))
    state["wheel"] = tmp_path / "agflow-0.1.3-py3-none-any.whl"
    return state


MCP = {"pid": 4242, "name": "python.exe", "cmd": "agflow.exe mcp serve"}


def test_lock_stops_agflow_and_retries_without_dropping_the_sdk(fake_install, capsys):
    s = fake_install
    s["results"] = [(1, LOCKED_OUTPUT), (0, "Installed 1 executable: agflow")]
    s["procs"] = [MCP]
    sdk, stopped = easy.tool_install("uv", ["uv", "tool", "install"], s["wheel"], True, assume_yes=False)
    assert sdk is True and stopped is True and s["stopped"] == [4242]
    assert all(c.startswith("agflow[sdk] @") for c in s["calls"])  # never fell back to "no SDK"
    out = capsys.readouterr().out
    assert "not available" not in out and "has no package" not in out
    assert "agflow MCP server" in out


def test_preflight_release_and_decline(fake_install):
    s = fake_install
    s["procs"] = [MCP]
    assert easy.release_agflow("uv", assume_yes=True) is True and s["stopped"] == [4242]
    s["procs"], s["confirm"] = [MCP], False
    with pytest.raises(easy.InstallFailed, match="Close Google Antigravity"):
        easy.release_agflow("uv", assume_yes=False)
    s["procs"] = []
    assert easy.release_agflow("uv", assume_yes=False) is False


def test_persistent_lock_gives_exact_instructions(fake_install):
    s = fake_install
    s["results"] = [(1, LOCKED_OUTPUT)] * 3  # nothing agflow-owned found: e.g. antivirus / Explorer window
    with pytest.raises(easy.InstallFailed, match="files of the installed agflow are still in use") as exc:
        easy.tool_install("uv", ["uv"], s["wheel"], True, assume_yes=True)
    assert "tools" in str(exc.value) and len(s["calls"]) == 3


def test_sdk_fallback_only_when_the_sdk_is_the_problem(fake_install):
    s = fake_install
    s["results"] = [(1, SDK_OUTPUT), (0, "ok")]
    assert easy.tool_install("uv", ["uv"], s["wheel"], True, assume_yes=True) == (False, False)
    assert s["calls"][0].startswith("agflow[sdk]") and s["calls"][1].startswith("agflow @")
    s["results"] = [(1, "error: Failed to fetch: https://pypi.org/simple/pydantic/")]
    with pytest.raises(easy.InstallFailed, match="reason is in uv's output"):
        easy.tool_install("uv", ["uv"], s["wheel"], True, assume_yes=True)
    assert len(s["calls"]) == 3  # no pointless retry without the SDK


def test_run_stream_echoes_and_returns_output(capsys):
    import sys as _sys
    script = "import sys; print('to-stdout'); sys.stderr.write('to-stderr\\n')"
    code, out = easy.run_stream([_sys.executable, "-c", script])
    assert code == 0 and "to-stdout" in out and "to-stderr" in out
    assert "to-stdout" in capsys.readouterr().out


# --- F4: the uv bootstrap is pinned and checksum-verified, never piped into a shell ----------------------------------

import hashlib  # noqa: E402
import os  # noqa: E402

SCRIPTS = REPO / "scripts"
PIN = SCRIPTS / "uv-pin.env"
INSTALL_UV_SH = SCRIPTS / "install-uv.sh"
INSTALL_UV_PS1 = SCRIPTS / "install-uv.ps1"
PIPE_TO_SHELL = re.compile(r"\|\s*(sudo\s+)?(ba|z|da)?sh\b|\|\s*(iex|invoke-expression)\b", re.IGNORECASE)


def read_pin() -> dict[str, str]:
    return dict(line.split("=", 1) for line in PIN.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#"))


def code_lines(path):
    """Lines that are not comments."""
    return [ln for ln in path.read_text(encoding="utf-8").splitlines()
            if not ln.lstrip().startswith(("#", "rem ", "REM ", "::"))]


def test_no_pipe_to_shell_anywhere_in_the_installer():
    for f in (BAT, INSTALL_UV_SH, INSTALL_UV_PS1):
        offenders = [ln for ln in code_lines(f) if PIPE_TO_SHELL.search(ln)]
        assert not offenders, f"{f.name} pipes a download into a shell: {offenders}"


def test_batch_half_runs_the_verified_script_not_the_cdn():
    batch = "\n".join(code_lines(BAT))
    assert "install-uv.ps1" in batch and "install-uv.sh" in batch
    assert "astral.sh/uv/install" not in batch


def test_pin_file_is_wellformed():
    pin = read_pin()
    assert re.fullmatch(r"\d+\.\d+\.\d+", pin["UV_VERSION"])
    assert re.fullmatch(r"[0-9a-f]{64}", pin["UV_SH_SHA256"])
    assert re.fullmatch(r"[0-9a-f]{64}", pin["UV_PS1_SHA256"])
    assert pin["UV_SH_SHA256"] != pin["UV_PS1_SHA256"]
    assert b"\r" not in PIN.read_bytes(), "the pin file is parsed by sh: it must stay LF-only"
    assert "scripts/uv-pin.env text eol=lf" in (REPO / ".gitattributes").read_text()


def run_installer_script(cmd, tmp_path, *, installer_text, url_hash="match", extra_env=None):
    """Run an install-uv script against a local fake installer served over file://."""
    fake = tmp_path / "fake-installer"
    fake.write_text(installer_text, encoding="utf-8", newline="\n")
    digest = hashlib.sha256(fake.read_bytes()).hexdigest()
    env = {**os.environ, "AGFLOW_UV_INSTALLER_URL": fake.as_uri(), "HOME": str(tmp_path), "USERPROFILE": str(tmp_path),
           "MARKER": str(tmp_path / "ran.marker")}
    for k in ("AGFLOW_UV_VERSION", "AGFLOW_UV_INSTALLER_SHA256"):
        env.pop(k, None)
    if url_hash == "match":
        env["AGFLOW_UV_VERSION"], env["AGFLOW_UV_INSTALLER_SHA256"] = "9.9.9", digest
    elif url_hash == "mismatch":
        env["AGFLOW_UV_VERSION"], env["AGFLOW_UV_INSTALLER_SHA256"] = "9.9.9", "0" * 64
    env.update(extra_env or {})
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)
    return proc, tmp_path / "ran.marker"


needs_sh_and_curl = pytest.mark.skipif(not (shutil.which("sh") and shutil.which("curl")), reason="needs sh and curl")
FAKE_SH = '#!/bin/sh\necho ran > "$MARKER"\n'


@needs_sh_and_curl
def test_sh_bootstrap_runs_the_installer_only_after_the_checksum_matches(tmp_path):
    proc, marker = run_installer_script(["sh", str(INSTALL_UV_SH)], tmp_path, installer_text=FAKE_SH)
    assert proc.returncode == 0, proc.stderr
    assert marker.exists()


@needs_sh_and_curl
def test_sh_bootstrap_refuses_a_tampered_installer(tmp_path):
    proc, marker = run_installer_script(["sh", str(INSTALL_UV_SH)], tmp_path, installer_text=FAKE_SH,
                                        url_hash="mismatch")
    assert proc.returncode != 0
    assert "does not match" in proc.stderr and "0" * 64 in proc.stderr
    assert not marker.exists(), "an installer that fails verification must never run"


@needs_sh_and_curl
def test_sh_bootstrap_uses_the_pinned_checksum_by_default(tmp_path):
    proc, marker = run_installer_script(["sh", str(INSTALL_UV_SH)], tmp_path, installer_text=FAKE_SH, url_hash="pin")
    assert proc.returncode != 0 and not marker.exists()  # the fake installer cannot match the pinned digest
    assert read_pin()["UV_SH_SHA256"] in proc.stderr and read_pin()["UV_VERSION"] in proc.stderr


@needs_sh_and_curl
def test_sh_bootstrap_version_override_requires_a_checksum(tmp_path):
    proc, marker = run_installer_script(["sh", str(INSTALL_UV_SH)], tmp_path, installer_text=FAKE_SH,
                                        url_hash="pin", extra_env={"AGFLOW_UV_VERSION": "9.9.9"})
    assert proc.returncode != 0 and not marker.exists()
    assert "AGFLOW_UV_INSTALLER_SHA256" in proc.stderr


@needs_sh_and_curl
def test_sh_bootstrap_reports_an_unreachable_download(tmp_path):
    proc, marker = run_installer_script(["sh", str(INSTALL_UV_SH)], tmp_path, installer_text=FAKE_SH,
                                        extra_env={"AGFLOW_UV_INSTALLER_URL": (tmp_path / "missing").as_uri()})
    assert proc.returncode != 0 and not marker.exists() and "could not download" in proc.stderr


needs_powershell = pytest.mark.skipif(os.name != "nt" or not shutil.which("powershell"),
                                      reason="Windows PowerShell only")
FAKE_PS1 = 'Set-Content -Path $env:MARKER -Value ran\n'


def ps(tmp_path, **kw):
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(INSTALL_UV_PS1)]
    return run_installer_script(cmd, tmp_path, installer_text=FAKE_PS1, **kw)


@needs_powershell
def test_powershell_bootstrap_runs_the_installer_only_after_the_checksum_matches(tmp_path):
    proc, marker = ps(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert marker.exists()


@needs_powershell
def test_powershell_bootstrap_refuses_a_tampered_installer(tmp_path):
    proc, marker = ps(tmp_path, url_hash="mismatch")
    assert proc.returncode != 0 and not marker.exists()
    assert "does not match" in proc.stderr


@needs_powershell
def test_powershell_bootstrap_uses_the_pinned_checksum_by_default(tmp_path):
    proc, marker = ps(tmp_path, url_hash="pin")
    assert proc.returncode != 0 and not marker.exists()
    assert read_pin()["UV_PS1_SHA256"] in proc.stderr


@needs_powershell
def test_powershell_bootstrap_version_override_requires_a_checksum(tmp_path):
    proc, marker = ps(tmp_path, url_hash="pin", extra_env={"AGFLOW_UV_VERSION": "9.9.9"})
    assert proc.returncode != 0 and not marker.exists() and "AGFLOW_UV_INSTALLER_SHA256" in proc.stderr
