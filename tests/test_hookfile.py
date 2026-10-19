"""hooks.json (H1): the layout Antigravity documents, the old one agflow wrote, and the fail-open command line.

The commands are *executed* here the way Antigravity runs them (`cmd /c <line>` on Windows, `sh -c <line>` elsewhere)
because the whole point of the wrapper is what the OS shell does with it; no string comparison proves that."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agflow import hookfile, platform_vocab
from agflow import install as inst
from tests.conftest import PLUGIN, write

WINDOWS = os.name == "nt"


def shell(line: str, stdin: bytes = b"", cwd: Path | None = None) -> subprocess.CompletedProcess[bytes]:
    argv = ["cmd", "/c", line] if WINDOWS else ["sh", "-c", line]
    return subprocess.run(argv, input=stdin, capture_output=True, cwd=cwd, timeout=60, check=False)


def quote_free(path: Path) -> str:
    """The path as a command-line word that needs no quotes (the repo may live under a path with spaces)."""
    text = str(path)
    if " " not in text:
        return text
    short = inst._windows_short_path(text)
    if short is None or " " in short:
        pytest.skip("this volume has no 8.3 short names and the path has spaces: nothing quote-free to run")
    return short


def script(tmp_path: Path, name: str, body: str) -> str:
    return f"{quote_free(Path(sys.executable))} {quote_free(write(tmp_path / name, body))}"


# --- the layouts ----------------------------------------------------------------------------------------------------

DOCUMENTED = {"agflow": {
    "PreToolUse": [{"matcher": "run_command", "hooks": [{"type": "command", "command": "agflow hook pre-tool-use"}]}],
    "PostToolUse": [{"matcher": ".*", "hooks": [{"command": "a"}, {"command": "b"}]}],
    "Stop": [{"type": "command", "command": "agflow hook stop", "timeout": 10}],
}}
LEGACY = {"hooks": {
    "PreToolUse": [{"matcher": "run_command", "command": "agflow hook pre-tool-use"}],
    "PostToolUse": [{"matcher": ".*", "command": "agflow hook post-tool-use"}],
    "Stop": [{"command": "agflow hook stop"}],
}}


def test_the_documented_layout_is_read_handler_by_handler():
    refs = hookfile.handlers(DOCUMENTED)
    assert [(r.hook, r.event, r.number, r.handler["command"]) for r in refs] == [
        ("agflow", "PreToolUse", 0, "agflow hook pre-tool-use"), ("agflow", "PostToolUse", 0, "a"),
        ("agflow", "PostToolUse", 1, "b"), ("agflow", "Stop", 0, "agflow hook stop")]
    assert refs[0].matcher == "run_command" and refs[0].where == "PreToolUse[0].hooks[0]"
    assert refs[3].where == "Stop[0]" and refs[3].matcher is None
    assert not any(r.inert for r in refs) and all(r.enabled for r in refs)


def test_the_layout_agflow_used_to_write_is_flagged_inert_except_stop():
    """Antigravity reads the old file as a hook named "hooks"; its tool-event entries are no groups, so never run."""
    refs = {r.event: r for r in hookfile.handlers(LEGACY)}
    assert refs["PreToolUse"].inert and refs["PostToolUse"].inert
    assert not refs["Stop"].inert, "a flat Stop entry is a valid handler, which is why only Stop ever ran"


def test_a_group_under_a_flat_event_is_inert_and_an_unknown_event_is_not_judged():
    data = {"x": {"Stop": [{"hooks": [{"command": "c"}]}], "Brand New": [{"command": "c"}]}}
    inert = {r.event: r.inert for r in hookfile.handlers(data)}
    assert inert == {"Stop": True, "Brand New": False}


def test_a_disabled_named_hook_is_marked_and_garbage_is_skipped_without_raising():
    refs = hookfile.handlers({"off": {"enabled": False, "Stop": [{"command": "c"}]}, "bad": 5, "worse": {"Stop": 7},
                              "odd": {"Stop": [3, {"command": "ok"}]}})
    assert [(r.hook, r.enabled) for r in refs] == [("off", False), ("odd", True)]
    for junk in (None, [], "x", 3):
        assert hookfile.handlers(junk) == []


def test_grouping_comes_from_the_vocabulary_not_from_this_module(workspace):
    override = write(workspace / ".agents" / "agflow" / "vocab.json",
                     json.dumps({"hook_events_grouped": {"add": ["Stop"]}}))
    vocab = platform_vocab.current(workspace, include_global=False)
    assert override in vocab.overrides and "Stop" in vocab.hook_events_grouped
    assert hookfile.handlers(LEGACY, vocab)[2].inert, "with Stop declared grouped, a plain Stop handler is inert"


def test_the_bundled_template_is_the_documented_layout():
    data = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    refs = hookfile.handlers(data)
    assert {r.event for r in refs} == {"PreToolUse", "PostToolUse", "Stop"} and not any(r.inert for r in refs)
    assert all(r.handler["command"].startswith("agflow hook ") and 0 < r.handler["timeout"] <= 10 for r in refs)
    for r in refs[:2]:
        assert r.matcher and r.matcher != ".*", "tool hooks name the tools they care about"


def test_is_agflow_command_recognises_what_the_installer_writes():
    for line in ("agflow hook stop", '"C:\\Program Files\\x\\agflow.exe" hook stop',
                 "/usr/bin/agflow hook pre-tool-use",
                 "python -m agflow hook post-tool-use", hookfile.fail_open("agflow hook stop", windows=True),
                 hookfile.fail_open("'/home/a b/agflow' hook stop", windows=False)):
        assert hookfile.is_agflow_command(line), line
    for line in ("agflow doctor", "node hook.js", "echo agflow", None, 3):
        assert not hookfile.is_agflow_command(line), line


# --- fail open: the shell really does what the wrapper promises -----------------------------------------------------


def test_fail_open_line_shapes():
    assert hookfile.fail_open("agflow hook stop", windows=True) == (
        "agflow hook stop || (echo agflow hook failed - continuing without it 1>&2 & echo {})")
    assert hookfile.fail_open("agflow hook stop", windows=False) == (
        "agflow hook stop || { echo 'agflow hook failed - continuing without it' >&2; echo '{}'; }")


def test_a_working_command_passes_through_untouched_and_gets_the_payload(tmp_path):
    line = script(tmp_path, "ok.py", "import sys\nprint('{\"seen\": %d}' % len(sys.stdin.read()))\n")
    done = shell(hookfile.fail_open(line), stdin=b'{"toolCall": {}}')
    assert done.returncode == 0 and json.loads(done.stdout) == {"seen": 16}
    assert hookfile.FAILOPEN_MARKER.encode() not in done.stderr


@pytest.mark.parametrize("what", ["exits_1", "crashes", "missing_executable"])
def test_a_failing_hook_command_answers_empty_json_with_exit_0_and_leaves_a_note(tmp_path, what):
    """The reported failure: a stale `agflow.exe` that exits 1 made Antigravity say 'Stop hook failed'."""
    lines = {
        "exits_1": script(tmp_path, "one.py", "import sys\nsys.exit(1)\n"),
        "crashes": script(tmp_path, "boom.py", "raise ModuleNotFoundError(\"No module named 'agflow'\")\n"),
        "missing_executable": "agflow-no-such-executable hook stop",
    }
    assert shell(lines[what], stdin=b"{}").returncode != 0, "without the wrapper Antigravity sees a failure"
    done = shell(hookfile.fail_open(lines[what]), stdin=b"{}")
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {}
    assert hookfile.FAILOPEN_MARKER in done.stderr.decode("utf-8", "replace")


def test_a_command_that_prints_partial_output_then_fails_still_ends_with_the_fallback(tmp_path):
    """Documented limit, pinned: the hook runner would see the partial text first. agflow's own hook prints exactly one
    line at the very end, so this cannot happen to it -- but the test says what the wrapper does *not* promise."""
    line = script(tmp_path, "partial.py", "import sys\nprint('{\"decision\": \"deny\"}')\nsys.exit(3)\n")
    done = shell(hookfile.fail_open(line))
    assert done.returncode == 0 and done.stdout.split() == [b'{"decision":', b'"deny"}', b"{}"]


@pytest.mark.skipif(not WINDOWS, reason="the 8.3 short-name path is a Windows thing")
def test_a_windows_path_with_spaces_is_run_through_its_short_name_or_fails_open(tmp_path):
    spaced = tmp_path / "my tools"
    exe = write(spaced / "hook.py", "print('{\"ran\": true}')\n")
    short = inst._windows_short_path(str(exe))
    if short is None:
        pytest.skip("this volume creates no 8.3 short names")
    line = inst.format_command([sys.executable, short], windows=True)
    done = shell(hookfile.fail_open(line))
    assert done.returncode == 0 and json.loads(done.stdout.strip().splitlines()[-1]) in ({"ran": True}, {})
    # the quoted form is what cmd.exe mishandles when the line arrives as one quoted argument: it must still fail open
    quoted = hookfile.fail_open(f'"{exe}" hook stop')
    again = shell(quoted)
    assert again.returncode == 0 and json.loads(again.stdout.strip().splitlines()[-1]) == {}


def test_the_bundled_matchers_select_the_tools_each_hook_exists_for():
    """The guard looks at commands and at files (a read of `.env` asks); the ledger records what changes things. Both
    are checked the strict way (the whole name) and the loose way (anywhere in it), since the engine's rule is not
    documented beyond 'a regex on the tool name'."""
    import re

    vocab = platform_vocab.builtin()
    data = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))["agflow"]
    pre, post = (re.compile(data[event][0]["matcher"]) for event in ("PreToolUse", "PostToolUse"))
    guarded = [*vocab.command_tools, *vocab.file_tools, "code_action"]
    recorded = [*vocab.command_tools, "write_to_file", "replace_file_content", "multi_replace_file_content",
                vocab.subagent_tool, "code_action"]
    for name in guarded:
        assert pre.fullmatch(name) and pre.search(name), name
    for name in recorded:
        assert post.fullmatch(name) and post.search(name), name
    for name in ("list_dir", "grep_search", "find_by_name", "search_web", "read_url_content", "notify_user"):
        assert not pre.search(name) and not post.search(name), f"{name} would start a process for nothing"
    assert not post.search("view_file"), "reading a file is not worth a process start"


# --- installer_shaped: the only lines `agflow doctor` will run -------------------------------------------------------

ACCEPTED = [
    ("agflow hook stop", True), ("agflow hook pre-tool-use", False),
    (r"C:\Users\KOOSHA~1\.local\bin\agflow.exe hook post-tool-use", True),
    (r'"C:\Program Files (x86)\agflow\agflow.exe" hook stop', True),
    (r"C:\Python314\python.exe -m agflow hook stop", True),
    (r"C:\Users\محمد\.local\bin\agflow.exe hook stop", True),
    ("/usr/local/bin/agflow hook stop", False), ("'/home/a b/.local/bin/agflow' hook stop", False),
    ("/usr/bin/python3 -m agflow hook pre-tool-use", False),
]
REJECTED = [
    "agflow hook stop; rm -rf ~", "agflow hook stop && id", "agflow hook stop | tee x", "agflow hook stop > f",
    "agflow hook stop\nid", "agflow hook unknown-event", "agflow hook", "agflow", "agflow doctor",
    "$(id) hook stop", "`id` hook stop", "%COMSPEC% hook stop", "$HOME/agflow hook stop", "FOO=1 agflow hook stop",
    "agflow --version hook stop", "sh -c agflow hook stop", "./agflow hook stop ", " agflow hook stop",
    "agflow hook stop || echo pwned", "agflow hook stop || (echo agflow hook failed 1>&2 & echo {}) & id",
    '"C:\\x\\agflow.exe" & id" hook stop', "'/x/agflow'; id' hook stop",
]


@pytest.mark.parametrize("line, windows", ACCEPTED)
def test_installer_shaped_accepts_what_the_installer_writes(line, windows):
    assert hookfile.installer_shaped(line, windows=windows), line
    assert hookfile.installer_shaped(hookfile.fail_open(line, windows=windows), windows=windows), "with the tail"


@pytest.mark.parametrize("line", REJECTED)
def test_installer_shaped_rejects_everything_else(line):
    for windows in (True, False):
        assert not hookfile.installer_shaped(line, windows=windows), line
        assert not hookfile.installer_shaped(hookfile.fail_open(line, windows=windows), windows=windows), line


def test_the_tail_must_be_exactly_the_one_for_this_os_and_nothing_follows_it():
    ours = hookfile.fail_open("agflow hook stop", windows=True)
    assert hookfile.installer_shaped(ours, windows=True) and not hookfile.installer_shaped(ours, windows=False)
    assert not hookfile.installer_shaped(ours + " & id", windows=True)
    assert not hookfile.installer_shaped(ours.replace("echo {}", "echo {} & id"), windows=True)
    assert not hookfile.installer_shaped(None) and not hookfile.installer_shaped(["agflow", "hook", "stop"])


def test_everything_the_installer_writes_for_this_machine_is_installer_shaped(home):
    """Not just the samples above: the real output of `agflow install`, whatever path this machine gives it."""
    from agflow import paths
    from agflow.install import install

    install("global")
    data = json.loads((paths.global_layout().plugins_dir / "agflow" / "hooks.json").read_text(encoding="utf-8"))
    commands = [r.handler["command"] for r in hookfile.handlers(data)]
    assert len(commands) == 3 and all(hookfile.installer_shaped(c) for c in commands), commands
