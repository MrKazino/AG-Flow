"""`agflow update ...`: exit codes, confirmation, what is shown, and the two internal commands an update relies on.

The release feed, the plan and the apply step are replaced by small stand-ins; their behaviour is tested elsewhere."""

from __future__ import annotations

import json
import re
import sys

import pytest
from typer.testing import CliRunner

from agflow import __version__, compat, update, update_apply
from agflow.cli import app
from agflow.release_feed import FeedError

runner = CliRunner()


def run(*args: str, input: str | None = None):
    return runner.invoke(app, ["update", *args], input=input, catch_exceptions=False)


def info(version="0.4.0") -> update.ReleaseInfo:
    return update.ReleaseInfo(version, f"v{version}", "2026-10-16T00:00:00Z", False, "notes", "w.whl", 10)


def result(status: str, **kw) -> update.CheckResult:
    return update.CheckResult(status, __version__, kw.pop("latest", info()), **kw)


def fake_plan(ok: bool = True, **kw) -> update.Plan:
    mode = update.InstallMode("venv", update.Path("/x/agflow"), update.Path("/x"), sys.executable)
    plan = update.Plan(__version__, "0.4.0", "v0.4.0", "minor", "Release notes line 1.\nLine 2.", "w.whl", 10, mode,
                       steps=list(update.STEPS), **kw)
    if not ok:
        plan.blocked.append("an Antigravity that is too old")
    return plan


# --- check ------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("status, code, words", [
    ("up-to-date", 0, "up to date"), ("available", 1, "is available"), ("error", 2, "Could not check"),
    ("none-published", 0, "No installable"), ("disabled", 0, "switched off"), ("ahead", 0, "newer than"),
])
def test_check_exit_codes_and_messages(monkeypatch, status, code, words):
    monkeypatch.setattr(update, "check", lambda **kw: result(status, error="offline" if status == "error" else ""))
    out = run("check")
    assert out.exit_code == code and words in out.output


def test_check_quiet_speaks_only_when_there_is_news(monkeypatch):
    monkeypatch.setattr(update, "check", lambda **kw: result("up-to-date"))
    assert run("check", "--quiet").output.strip() == ""
    monkeypatch.setattr(update, "check", lambda **kw: result("available"))
    assert "is available" in run("check", "--quiet").output


def test_check_passes_the_flags_through_and_can_print_json(monkeypatch):
    seen = {}
    monkeypatch.setattr(update, "check", lambda **kw: seen.update(kw) or result("up-to-date"))
    data = json.loads(run("check", "--force", "--auto", "--json").output)
    assert seen == {"force": True, "automatic": True} and data["status"] == "up-to-date"


# --- plan -------------------------------------------------------------------------------------------------------------


def test_plan_shows_steps_and_release_notes(monkeypatch):
    monkeypatch.setattr(update, "plan", lambda **kw: fake_plan())
    out = run("plan")
    assert out.exit_code == 0 and "0.4.0 (minor)" in out.output and "1. download the wheel" in out.output
    assert "release notes:" in out.output and "Release notes line 1." in out.output


def test_plan_that_is_blocked_exits_one_and_says_why(monkeypatch):
    monkeypatch.setattr(update, "plan", lambda **kw: fake_plan(ok=False))
    out = run("plan")
    assert out.exit_code == 1 and "blocked: an Antigravity that is too old" in out.output
    assert "1. download" not in out.output, "steps are shown only for a plan that can proceed"


def test_plan_json_and_options(monkeypatch):
    seen = {}
    monkeypatch.setattr(update, "plan", lambda **kw: seen.update(kw) or fake_plan())
    data = json.loads(run("plan", "--json", "--to", "0.4.0", "--allow-breaking", "--reinstall").output)
    assert seen == {"target": "0.4.0", "allow_breaking": True, "reinstall": True} and data["target"] == "0.4.0"


def test_a_feed_error_is_a_plain_message_not_a_traceback(monkeypatch):
    def boom(**kw):
        raise FeedError("nothing is published yet")

    monkeypatch.setattr(update, "plan", boom)
    out = runner.invoke(app, ["update", "plan"])
    assert out.exit_code == 1 and "nothing is published yet" in out.output
    assert isinstance(out.exception, SystemExit), "a clean exit, not a traceback"


# --- apply ------------------------------------------------------------------------------------------------------------


def outcome(**kw) -> update_apply.Outcome:
    return update_apply.Outcome(kw.pop("ok", True), __version__, "0.4.0", **kw)


def test_apply_asks_first_and_declining_changes_nothing(monkeypatch):
    called = []
    monkeypatch.setattr(update, "plan", lambda **kw: fake_plan())
    monkeypatch.setattr(update_apply, "apply", lambda *a, **k: called.append(1) or outcome())
    out = run("apply", input="n\n")
    assert out.exit_code == 0 and "Nothing changed." in out.output and called == []


def test_apply_with_yes_runs_and_reports_success(monkeypatch):
    seen = {}

    def fake_apply(plan, **kw):
        kw["log"]("installed AG-Flow 0.4.0")
        seen.update(kw)
        return outcome(merge={"global": ["kept your edit of skills/tdd/SKILL.md"]})

    monkeypatch.setattr(update, "plan", lambda **kw: fake_plan())
    monkeypatch.setattr(update_apply, "apply", fake_apply)
    out = run("apply", "--yes")
    assert out.exit_code == 0 and "installed AG-Flow 0.4.0" in out.output
    assert "[global] kept your edit of skills/tdd/SKILL.md" in out.output
    assert "AG-Flow 0.4.0 is installed" in out.output and "agflow sync check" in out.output


def test_apply_confirmed_interactively(monkeypatch):
    monkeypatch.setattr(update, "plan", lambda **kw: fake_plan())
    monkeypatch.setattr(update_apply, "apply", lambda *a, **k: outcome())
    assert "is installed" in run("apply", input="y\n").output


def test_apply_reports_a_rolled_back_failure_and_exits_one(monkeypatch):
    monkeypatch.setattr(update, "plan", lambda **kw: fake_plan())
    monkeypatch.setattr(update_apply, "apply", lambda *a, **k: outcome(ok=False, rolled_back=True,
                                                                       problems=["the new version fails its checks"]))
    out = run("apply", "--yes")
    assert out.exit_code == 1 and "failed and was undone" in out.output and "fails its checks" in out.output


def test_apply_refuses_a_blocked_plan_without_asking(monkeypatch):
    monkeypatch.setattr(update, "plan", lambda **kw: fake_plan(ok=False))
    monkeypatch.setattr(update_apply, "apply", lambda *a, **k: pytest.fail("must not run"))
    out = run("apply", "--yes")
    assert out.exit_code == 1 and "blocked" in out.output


def test_apply_turns_a_held_lock_into_a_message(monkeypatch):
    def locked(*a, **k):
        raise update_apply.UpdateError("another update is running")

    monkeypatch.setattr(update, "plan", lambda **kw: fake_plan())
    monkeypatch.setattr(update_apply, "apply", locked)
    out = run("apply", "--yes")
    assert out.exit_code == 1 and "another update is running" in out.output


# --- rollback ---------------------------------------------------------------------------------------------------------


def test_rollback_confirms_and_reports(monkeypatch):
    restored = update_apply.Outcome(True, "0.4.0", "0.3.0", rolled_back=True)
    monkeypatch.setattr(update_apply, "rollback", lambda **kw: restored)
    assert run("rollback", input="n\n").output.count("Nothing changed.") == 1
    out = run("rollback", "--yes")
    assert out.exit_code == 0 and "AG-Flow 0.3.0 is restored" in out.output


def test_rollback_failures_exit_one(monkeypatch):
    monkeypatch.setattr(update_apply, "rollback", lambda **kw: update_apply.Outcome(False, "0.4.0", "0.3.0",
                                                                                    problems=["disk full"]))
    out = run("rollback", "--yes")
    assert out.exit_code == 1 and "disk full" in out.output

    def none(**kw):
        raise update_apply.UpdateError("there is no update to roll back")

    monkeypatch.setattr(update_apply, "rollback", none)
    assert "no update to roll back" in run("rollback", "--yes").output


# --- status and config ------------------------------------------------------------------------------------------------


def test_status_before_any_check():
    data = json.loads(run("status", "--json").output)
    assert data["version"] == __version__ and data["latest"] is None and data["unfinished"] is False
    assert data["config"]["channel"] == "stable" and data["last_apply"] is None
    text = run("status").output
    assert f"AG-Flow {__version__}" in text and "automatic checks on" in text


def test_status_after_a_check_and_a_failed_update():
    update.save_state({"checked_at": 1.0, "latest": info("0.5.0").to_json(), "error": "offline",
                       "last_apply": {"from": "0.3.0", "to": "0.4.0", "ok": False, "rolled_back": True}})
    update.journal_path().write_text('{"stage": "package-installed"}', encoding="utf-8")
    text = run("status").output
    assert "latest known release: 0.5.0" in text and "last check failed: offline" in text
    assert "failed and was undone" in text and "agflow update rollback" in text


def test_config_shows_changes_and_validates():
    assert json.loads(run("config").output)["channel"] == "stable"
    changed = json.loads(run("config", "--channel", "pre", "--no-auto-check", "--interval-hours", "6").output)
    assert changed == {"channel": "pre", "auto_check": False, "interval_hours": 6.0, "repo": update.DEFAULT_REPO}
    assert update.load_config().channel == "pre"
    assert run("config", "--channel", "nightly").exit_code == 1
    assert run("config", "--interval-hours", "0").exit_code == 1
    assert update.load_config().interval_hours == 6.0, "a rejected value changes nothing"


# --- the two internal commands an update relies on --------------------------------------------------------------------


def test_verify_reports_the_version_and_any_problems(monkeypatch):
    monkeypatch.setattr(compat, "installation_problems", lambda ws=None: [])
    ok = run("verify", "--json")
    assert ok.exit_code == 0 and json.loads(ok.output) == {"version": __version__, "problems": []}
    monkeypatch.setattr(compat, "installation_problems", lambda ws=None: ["hooks.broken: the Stop hook crashed"])
    bad = run("verify", "--json")
    assert bad.exit_code == 1 and json.loads(bad.output)["problems"] == ["hooks.broken: the Stop hook crashed"]
    assert "1 problem(s)" in run("verify").output


def test_the_internal_commands_are_hidden_from_help_but_exist():
    help_text = runner.invoke(app, ["update", "--help"]).output
    listed = [m.group(1) for line in help_text.splitlines() if (m := re.search(r"[│|]\s+(\w+)\s{2,}", line))]
    assert "verify" not in listed and "refresh" not in listed
    assert {"check", "plan", "apply", "rollback", "status", "config"} <= set(listed)


def test_refresh_installs_the_bundled_plugin_for_the_global_scope():
    out = run("refresh", "--scope", "global")
    assert out.exit_code == 0
    data = json.loads(out.output)
    assert data["scope"] == "global" and data["actions"] > 0


def test_refresh_needs_a_workspace_root_for_a_workspace_install(workspace):
    assert run("refresh", "--scope", "workspace").exit_code == 1
    ok = run("refresh", "--scope", "workspace", "--workspace-root", str(workspace), "--no-hooks", "--no-gemini-md")
    assert ok.exit_code == 0 and (workspace / ".agents" / "plugins" / "agflow" / "plugin.json").is_file()


def test_refresh_rejects_an_unknown_scope_and_reports_installer_errors(workspace):
    assert run("refresh", "--scope", "nowhere").exit_code == 1
    run("refresh", "--scope", "global")
    refused = run("refresh", "--scope", "workspace", "--workspace-root", str(workspace))
    assert refused.exit_code == 1 and "already installed" in refused.output   # a duplicate needs --allow-duplicate
    allowed = run("refresh", "--scope", "workspace", "--workspace-root", str(workspace), "--allow-duplicate")
    assert allowed.exit_code == 0
