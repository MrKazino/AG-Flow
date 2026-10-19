"""`agflow sync gemini-cli` checks Gemini CLI's side before it writes anything (F35)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agflow import paths
from agflow import sync as sync_module
from agflow.cli import app
from agflow.install import install
from agflow.sync import SyncError, probe_gemini_cli, sync_gemini_cli
from tests.conftest import write


@pytest.fixture(autouse=True)
def installed_plugin(home):
    """Something to sync (the bundled plugin's skills and MCP server) and no real Gemini CLI on PATH."""
    install("global", with_gemini_md=False)


@pytest.fixture(autouse=True)
def no_gemini_on_path(monkeypatch):
    monkeypatch.setattr(sync_module.shutil, "which", lambda name: None)


def settings_file() -> Path:
    return paths.gemini_cli_settings(None)


def skills_dir() -> Path:
    return paths.gemini_cli_skills_dir(None)


def track_file() -> Path:
    return paths.global_state_dir() / "sync-gemini-cli.json"


def snapshot() -> dict[str, bytes | None]:
    """Everything the sync could touch, as bytes (``None`` = absent); equal before and after = nothing was written."""
    out: dict[str, bytes | None] = {}
    for path in (settings_file(), track_file()):
        out[str(path)] = path.read_bytes() if path.exists() else None
    out[str(skills_dir())] = b"dir" if skills_dir().exists() else None
    return out


# --- a healthy target is unaffected ---------------------------------------------------------------------------------


def test_a_valid_target_is_synced_and_foreign_settings_survive():
    write(settings_file(), json.dumps({"theme": "dark", "mcpServers": {"mine": {"command": "x"}}}))
    report = sync_gemini_cli(None)
    data = json.loads(settings_file().read_text(encoding="utf-8"))
    assert data["theme"] == "dark" and data["mcpServers"]["mine"] == {"command": "x"}
    assert "agflow" in data["mcpServers"]
    assert (skills_dir() / "tdd" / "SKILL.md").is_file()
    assert any(a.startswith("skill") for a in report.actions)
    assert json.loads(track_file().read_text(encoding="utf-8"))["skills"]


def test_an_absent_target_is_created_and_an_empty_settings_file_is_fine():
    assert sync_gemini_cli(None).actions
    settings_file().unlink()
    write(settings_file(), "   \n")  # empty counts as {}
    assert probe_gemini_cli(settings_file(), skills_dir(), track_file()) == []


# --- every wrong shape is refused up front, and nothing is written -------------------------------------------------


@pytest.mark.parametrize(("content", "message"), [
    ('{"mcpServers": ["a", "b"]}', r"`mcpServers` in .*settings\.json must be an object .* found list"),
    ('{"mcpServers": null}', r"`mcpServers` .* found NoneType"),
    ('{"mcpServers": {"good": {"command": "x"}, "bad": 1, "worse": "y"}}', r"must map each server to an object; "
                                                                           r"these do not: bad, worse"),
    ('["not", "an", "object"]', r"must contain a JSON object, found list"),
    ('"just a string"', r"must contain a JSON object, found str"),
    ('{"mcpServers": {', r"is not valid JSON"),
])
def test_a_wrong_settings_file_is_refused_with_an_actionable_message_and_nothing_is_written(content, message):
    write(settings_file(), content)
    before = snapshot()
    with pytest.raises(SyncError, match=message) as err:
        sync_gemini_cli(None)
    assert str(settings_file()) in str(err.value), "the message must name the file"
    assert "Nothing was written" in str(err.value)
    assert snapshot() == before, "a refused sync must leave no partial result"


def test_a_dry_run_probes_too():
    write(settings_file(), '{"mcpServers": []}')
    with pytest.raises(SyncError, match="mcpServers"):
        sync_gemini_cli(None, dry_run=True)
    write(settings_file(), "{}")
    assert sync_gemini_cli(None, dry_run=True).actions  # and a healthy target still plans normally
    assert not skills_dir().exists(), "a dry run writes nothing"


def test_a_file_where_the_skills_directory_belongs_is_refused():
    write(skills_dir(), "i am a file")
    before = snapshot()
    with pytest.raises(SyncError, match=r"exists but is not a directory"):
        sync_gemini_cli(None)
    assert snapshot() == before and skills_dir().read_text(encoding="utf-8") == "i am a file"


def test_a_directory_where_the_settings_file_belongs_is_refused():
    settings_file().mkdir(parents=True)
    with pytest.raises(SyncError, match=r"is not a file"):
        sync_gemini_cli(None)


@pytest.mark.parametrize(("content", "message"), [
    ("{broken", r"record .* is not valid JSON"),
    ('{"skills": "tdd", "mcp": []}', r"must hold lists under skills"),
    ('["x"]', r"must contain a JSON object"),
])
def test_a_damaged_record_of_what_was_synced_is_refused(content, message):
    write(track_file(), content)
    before = snapshot()
    with pytest.raises(SyncError, match=message):
        sync_gemini_cli(None)
    assert snapshot() == before


def test_a_record_with_missing_keys_is_accepted():
    write(track_file(), '{"skills": []}')  # an older or hand-edited record without "mcp"
    assert sync_gemini_cli(None).actions


# --- the Gemini CLI version is context, never a gate ----------------------------------------------------------------


def test_the_version_is_reported_when_gemini_is_on_path(monkeypatch):
    monkeypatch.setattr(sync_module.shutil, "which", lambda name: "/opt/gemini")
    monkeypatch.setattr(sync_module.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 0, stdout="0.9.4\nextra line\n", stderr=""))
    assert sync_gemini_cli(None, dry_run=True).notes == ["Gemini CLI 0.9.4 (/opt/gemini)"]


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("gemini", 3), FileNotFoundError("gone"),
                                     PermissionError("denied")])
def test_a_version_check_that_fails_never_fails_the_sync(monkeypatch, failure):
    monkeypatch.setattr(sync_module.shutil, "which", lambda name: "/opt/gemini")

    def boom(cmd, **kw):
        raise failure

    monkeypatch.setattr(sync_module.subprocess, "run", boom)
    report = sync_gemini_cli(None, dry_run=True)
    assert report.actions and "did not answer" in report.notes[0]


def test_the_version_check_is_bounded_and_does_not_read_stdin(monkeypatch):
    seen = {}
    monkeypatch.setattr(sync_module.shutil, "which", lambda name: "/opt/gemini")

    def spy(cmd, **kw):
        seen.update(kw, cmd=cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="1\n", stderr="")

    monkeypatch.setattr(sync_module.subprocess, "run", spy)
    sync_gemini_cli(None, dry_run=True)
    assert seen["timeout"] == 3 and seen["stdin"] == subprocess.DEVNULL and seen["cmd"][1:] == ["--version"]


def test_nothing_is_run_when_gemini_is_not_installed(monkeypatch):
    monkeypatch.setattr(sync_module.subprocess, "run", lambda *a, **k: pytest.fail("must not run anything"))
    assert sync_gemini_cli(None, dry_run=True).notes == []


# --- the command line -----------------------------------------------------------------------------------------------


def test_the_command_reports_a_refusal_as_an_error_not_a_traceback(workspace):
    write(settings_file(), '{"mcpServers": []}')
    result = CliRunner().invoke(app, ["sync", "gemini-cli", "-w", str(workspace)])
    assert result.exit_code == 1
    assert "mcpServers" in result.output and "Nothing was written" in result.output
    assert "Traceback" not in result.output


def test_the_command_still_syncs_a_healthy_target(workspace):
    result = CliRunner().invoke(app, ["sync", "gemini-cli", "-w", str(workspace)])
    assert result.exit_code == 0, result.output
    assert "skill" in result.output and (skills_dir() / "tdd").is_dir()
