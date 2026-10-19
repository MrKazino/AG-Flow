"""`agflow sync check|apply|forget`: exit codes, quiet mode, what gets remembered, and that nothing is accepted when
it should not be. The Antigravity probe is replaced by synthetic snapshots; no real installation is read."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from agflow import antigravity_probe as ap
from agflow import compat
from agflow.cli import app

runner = CliRunner()
HOOKS = "agy-customizations/docs/hooks.md"


def run(*args: str):
    return runner.invoke(app, ["sync", *args], catch_exceptions=False)


def verified() -> ap.Snapshot:
    return ap.Snapshot.from_json(compat.shipped()["verified"])


@pytest.fixture
def antigravity(monkeypatch):
    """Control what the probe sees: ``antigravity.snapshot = ...``."""
    state = type("State", (), {"snapshot": verified()})()
    monkeypatch.setattr(ap, "probe", lambda *a, **k: state.snapshot)
    return state


def drifted() -> ap.Snapshot:
    snap = verified()
    snap.markers[HOOKS]["table_keys"] = sorted([*snap.markers[HOOKS]["table_keys"], "PreCompaction"])
    snap.doc_hashes[HOOKS] = "0" * 64
    return snap


def broken() -> ap.Snapshot:
    snap = verified()
    snap.markers[HOOKS]["table_keys"] = [k for k in snap.markers[HOOKS]["table_keys"] if k != "Stop"]
    snap.doc_hashes[HOOKS] = "0" * 64
    return snap


# --- check ------------------------------------------------------------------------------------------------------------


def test_check_ok_exits_zero_and_remembers_the_result(antigravity):
    result = run("check")
    assert result.exit_code == 0 and "compatible" in result.output
    assert compat.load_last().status == "ok"


def test_check_json_is_machine_readable(antigravity):
    data = json.loads(run("check", "--json").output)
    assert data["status"] == "ok" and data["baseline"] == "shipped" and data["findings"] == []


def test_check_quiet_prints_nothing_when_all_is_well_and_still_speaks_on_drift(antigravity):
    assert run("check", "--quiet").output.strip() == ""
    antigravity.snapshot = drifted()
    result = run("check", "--quiet")
    assert result.exit_code == 1 and "PreCompaction" in result.output


def test_check_exit_codes_follow_the_severity(antigravity):
    antigravity.snapshot = drifted()
    assert run("check").exit_code == 1
    antigravity.snapshot = broken()
    result = run("check")
    assert result.exit_code == 2 and "[breaking]" in result.output and "hooks" in result.output
    antigravity.snapshot = ap.Snapshot()
    assert run("check").exit_code == 0  # nothing installed is not an alarm


def test_check_no_save_leaves_no_trace(antigravity):
    run("check", "--no-save")
    assert compat.load_last() is None


# --- apply ------------------------------------------------------------------------------------------------------------


def test_apply_accepts_a_drifted_antigravity_when_the_installation_is_healthy(antigravity, monkeypatch):
    monkeypatch.setattr(compat, "installation_problems", lambda ws=None: [])
    antigravity.snapshot = drifted()
    assert run("check").exit_code == 1
    result = run("apply")
    assert result.exit_code == 0 and "Accepted" in result.output
    assert run("check").exit_code == 0 and compat.load_last().baseline == "accepted"


def test_apply_dry_run_writes_nothing(antigravity, monkeypatch):
    monkeypatch.setattr(compat, "installation_problems", lambda ws=None: [])
    antigravity.snapshot = drifted()
    result = run("apply", "--dry-run")
    assert result.exit_code == 0 and "dry run" in result.output
    assert not compat.accepted_path().exists()


def test_apply_refuses_while_the_installation_has_errors(antigravity, monkeypatch):
    monkeypatch.setattr(compat, "installation_problems", lambda ws=None: ["hooks.broken: the Stop hook crashed"])
    antigravity.snapshot = drifted()
    result = run("apply")
    assert result.exit_code == 1 and "hooks.broken" in result.output and "doctor --fix" in result.output
    assert not compat.accepted_path().exists()


def test_apply_refuses_when_something_agflow_depends_on_is_gone(antigravity, monkeypatch):
    monkeypatch.setattr(compat, "installation_problems", lambda ws=None: [])
    antigravity.snapshot = broken()
    result = run("apply")
    assert result.exit_code == 1 and "Not accepted" in result.output and not compat.accepted_path().exists()


def test_apply_with_nothing_changed_or_nothing_installed_says_so(antigravity):
    assert "Already verified" in run("apply").output
    antigravity.snapshot = ap.Snapshot()
    assert "Nothing to verify" in run("apply").output
    assert not compat.accepted_path().exists()


# --- forget -----------------------------------------------------------------------------------------------------------


def test_forget_returns_to_the_shipped_baseline(antigravity, monkeypatch):
    monkeypatch.setattr(compat, "installation_problems", lambda ws=None: [])
    antigravity.snapshot = drifted()
    run("apply")
    assert "removed" in run("forget").output and "no accepted" in run("forget").output
    assert run("check").exit_code == 1


def test_the_gemini_cli_sync_command_still_exists():
    assert "gemini-cli" in runner.invoke(app, ["sync", "--help"]).output
