"""Update checking and planning: cached and conditional, calm on failure, and a plan that names everything that blocks.

Runs against a fake GitHub on loopback (tests/fake_github.py); nothing touches the network or the installed package."""

from __future__ import annotations

import json
import os
import sys
import types

import pytest

from agflow import antigravity_probe as ap
from agflow import release_feed as rf
from agflow import update
from tests.fake_github import REPO, FakeGitHub

HOUR = 3600.0
real_installer_argv = update.installer_argv  # the autouse fixture below stubs update.installer_argv


@pytest.fixture
def gh():
    fake = FakeGitHub().start()
    yield fake
    fake.stop()


def feed(gh, **kw) -> rf.Feed:
    return rf.Feed(REPO, api=gh.base, allow_loopback=True, token=None, **kw)


@pytest.fixture
def site(tmp_path):
    (tmp_path / "site" / "agflow").mkdir(parents=True)
    return tmp_path / "site"


@pytest.fixture
def mode(site):
    return update.InstallMode("venv", site / "agflow", site, sys.executable)


def api_requests(gh) -> int:
    return sum(1 for r in gh.seen if r["path"].startswith(f"/repos/{REPO}/releases"))


# --- sanitize -----------------------------------------------------------------------------------


@pytest.mark.parametrize("raw, clean", [
    ("plain text", "plain text"),
    ("\x1b[31mred\x1b[0m and \x1b]0;title\x07done", "red and done"),
    ("bell\x07 and \x00 null and \x1b[2Jclear", "bell and  null and clear"),
    ("a\r\nb\rc", "a\nb\nc"),
    ("one\n\n\n\n\ntwo", "one\n\ntwo"),
    ("  padded  ", "padded"),
    (None, ""),
    (12, "12"),
])
def test_sanitize(raw, clean):
    assert update.sanitize(raw) == clean


def test_sanitize_bounds_the_length():
    out = update.sanitize("x" * 10_000, 100)
    assert len(out) == 100 and out.endswith("…")


# --- configuration ------------------------------------------------------------------------------


def test_default_configuration(tmp_path):
    cfg = update.load_config(tmp_path)
    assert (cfg.channel, cfg.auto_check, cfg.interval_hours, cfg.repo) == ("stable", True, 24.0, rf.DEFAULT_REPO)


def test_configuration_round_trips(tmp_path):
    cfg = update.Config("pre", False, 6.0, "me/fork")
    update.save_config(cfg, tmp_path)
    assert update.load_config(tmp_path) == cfg


@pytest.mark.parametrize("content", [
    "{broken", "[]", "42", '{"channel": "nightly"}', '{"auto_check": "yes"}', '{"interval_hours": 0}',
    '{"interval_hours": 100000}', '{"interval_hours": true}', '{"repo": "../evil"}', '{"repo": "a/b/c"}',
])
def test_bad_configuration_values_fall_back_to_defaults_one_by_one(tmp_path, content):
    (tmp_path / update.CONFIG_NAME).write_text(content, encoding="utf-8")
    assert update.load_config(tmp_path) == update.Config()


def test_one_bad_value_does_not_discard_the_good_ones(tmp_path):
    (tmp_path / update.CONFIG_NAME).write_text('{"channel": "pre", "interval_hours": -3, "auto_check": false}',
                                               encoding="utf-8")
    cfg = update.load_config(tmp_path)
    assert (cfg.channel, cfg.interval_hours, cfg.auto_check) == ("pre", 24.0, False)


# --- when a check is due ------------------------------------------------------------------------


@pytest.mark.parametrize("state, now, expected", [
    ({}, 1000.0, True),
    ({"checked_at": 1000.0}, 1000.0 + 23 * HOUR, False),
    ({"checked_at": 1000.0}, 1000.0 + 24 * HOUR, True),
    ({"checked_at": 1000.0, "error": "boom"}, 1000.0 + 0.5 * HOUR, False),
    ({"checked_at": 1000.0, "error": "boom"}, 1000.0 + 1 * HOUR, True),
    ({"checked_at": 5000.0}, 1000.0, True),   # the clock moved back
    ({"checked_at": "x"}, 1000.0, True),
])
def test_due(state, now, expected):
    assert update.due(state, update.Config(), now) is expected


def test_a_short_interval_also_shortens_the_retry_after_a_failure():
    cfg = update.Config(interval_hours=1.0)
    assert update.due({"checked_at": 0.0, "error": "x"}, cfg, 3600.0) is True
    cfg = update.Config(interval_hours=48.0)
    assert update.due({"checked_at": 0.0, "error": "x"}, cfg, 3599.0) is False


# --- check --------------------------------------------------------------------------------------


def test_the_first_check_asks_github_and_remembers_the_answer(gh, tmp_path):
    gh.add_release("0.4.0", notes="\x1b[31mnew things\x1b[0m")
    result = update.check(feed=feed(gh), directory=tmp_path, current="0.3.0", now=1000.0)
    assert result.status == "available" and result.available and not result.cached
    assert result.latest.version == "0.4.0" and result.latest.notes == "new things"
    state = update.load_state(tmp_path)
    assert state["checked_at"] == 1000.0 and state["etag"] and state["latest"]["version"] == "0.4.0"
    assert result.lines()[0].startswith("AG-Flow 0.4.0 is available")


def test_a_recent_check_is_answered_from_the_cache_without_any_request(gh, tmp_path):
    gh.add_release("0.4.0")
    update.check(feed=feed(gh), directory=tmp_path, current="0.3.0", now=1000.0)
    before = api_requests(gh)
    again = update.check(feed=feed(gh), directory=tmp_path, current="0.3.0", now=1000.0 + HOUR)
    assert again.cached and again.status == "available" and api_requests(gh) == before


def test_cached_answers_follow_the_running_version(gh, tmp_path):
    gh.add_release("0.4.0")
    update.check(feed=feed(gh), directory=tmp_path, current="0.3.0", now=1000.0)
    after_update = update.check(feed=feed(gh), directory=tmp_path, current="0.4.0", now=1000.0 + HOUR)
    assert after_update.cached and after_update.status == "up-to-date"


def test_a_forced_check_is_a_conditional_request_and_a_304_keeps_the_answer(gh, tmp_path):
    gh.add_release("0.4.0")
    update.check(feed=feed(gh), directory=tmp_path, current="0.3.0", now=1000.0)
    again = update.check(force=True, feed=feed(gh), directory=tmp_path, current="0.3.0", now=2000.0)
    assert not again.cached and again.latest.version == "0.4.0"
    assert gh.seen[-1]["headers"].get("if-none-match") == gh.etag
    assert update.load_state(tmp_path)["checked_at"] == 2000.0


def test_a_new_release_replaces_the_remembered_one(gh, tmp_path):
    gh.add_release("0.4.0")
    update.check(feed=feed(gh), directory=tmp_path, current="0.3.0", now=1000.0)
    gh.add_release("0.5.0")
    got = update.check(force=True, feed=feed(gh), directory=tmp_path, current="0.3.0", now=2000.0)
    assert got.latest.version == "0.5.0"


def test_changing_the_channel_does_not_reuse_the_cache_or_the_etag(gh, tmp_path):
    gh.add_release("0.4.0")
    gh.add_release("0.5.0rc1", prerelease=True)
    stable = update.check(feed=feed(gh), directory=tmp_path, config=update.Config(), current="0.3.0", now=1000.0)
    pre = update.check(feed=feed(gh), directory=tmp_path, config=update.Config(channel="pre"), current="0.3.0",
                       now=1000.0 + 60)
    assert stable.latest.version == "0.4.0" and pre.latest.version == "0.5.0rc1" and not pre.cached
    assert "if-none-match" not in gh.seen[-1]["headers"]


def test_a_failed_check_is_a_calm_result_with_the_last_known_answer(gh, tmp_path):
    gh.add_release("0.4.0")
    update.check(feed=feed(gh), directory=tmp_path, current="0.3.0", now=1000.0)
    gh.mode = "rate_limited"
    result = update.check(force=True, feed=feed(gh), directory=tmp_path, current="0.3.0", now=2000.0)
    assert result.status == "error" and "rate limit" in result.error and result.latest.version == "0.4.0"
    assert "Last known release: 0.4.0" in "\n".join(result.lines())
    assert update.load_state(tmp_path)["error"]


def test_after_a_failure_the_next_automatic_check_waits_an_hour_not_a_day(gh, tmp_path):
    gh.mode = "server_error"
    update.check(feed=feed(gh), directory=tmp_path, current="0.3.0", now=1000.0)
    n = api_requests(gh)
    update.check(feed=feed(gh), directory=tmp_path, current="0.3.0", now=1000.0 + 1800)
    assert api_requests(gh) == n
    gh.mode = "ok"
    gh.add_release("0.4.0")
    ok = update.check(feed=feed(gh), directory=tmp_path, current="0.3.0", now=1000.0 + HOUR + 1)
    assert ok.status == "available" and update.load_state(tmp_path)["error"] is None


def test_an_unreachable_server_is_an_error_not_an_exception(tmp_path):
    dead = rf.Feed(REPO, api="http://127.0.0.1:9", allow_loopback=True, timeout=1.0, token=None)
    result = update.check(feed=dead, directory=tmp_path, current="0.3.0")
    assert result.status == "error" and "could not reach" in result.error


def test_background_checks_respect_the_off_switch_but_a_forced_one_does_not(gh, tmp_path):
    gh.add_release("0.4.0")
    off = update.Config(auto_check=False)
    quiet = update.check(automatic=True, feed=feed(gh), directory=tmp_path, config=off, current="0.3.0")
    assert quiet.status == "disabled" and api_requests(gh) == 0
    forced = update.check(automatic=True, force=True, feed=feed(gh), directory=tmp_path, config=off,
                          current="0.3.0")
    assert forced.status == "available"


@pytest.mark.parametrize("current, status", [("0.4.0", "up-to-date"), ("0.5.0", "ahead"), ("0.3.9", "available"),
                                              ("weird", "unknown")])
def test_statuses(gh, tmp_path, current, status):
    gh.add_release("0.4.0")
    assert update.check(feed=feed(gh), directory=tmp_path, current=current).status == status


def test_a_repository_without_installable_releases(gh, tmp_path):
    gh.add_release("0.4.0", sums=False)   # cannot be verified
    gh.add_release("0.4.1", draft=True)
    result = update.check(feed=feed(gh), directory=tmp_path, current="0.3.0")
    assert result.status == "none-published" and result.latest is None


def test_a_damaged_state_file_is_ignored(gh, tmp_path):
    (tmp_path / update.STATE_NAME).write_text("{broken", encoding="utf-8")
    gh.add_release("0.4.0")
    assert update.check(feed=feed(gh), directory=tmp_path, current="0.3.0").status == "available"


# --- how AG-Flow is installed -------------------------------------------------------------------


def test_a_source_checkout_is_not_updatable(tmp_path):
    (tmp_path / "proj" / "src" / "agflow").mkdir(parents=True)
    (tmp_path / "proj" / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    mode = update.detect_install_mode(tmp_path / "proj" / "src" / "agflow")
    assert mode.kind == "source" and not mode.updatable


def test_an_editable_install_is_not_updatable(monkeypatch):
    dist = types.SimpleNamespace(read_text=lambda name: json.dumps({"dir_info": {"editable": True}}))
    monkeypatch.setattr(update.metadata, "distribution", lambda name: dist)
    mode = update.detect_install_mode()
    assert mode.kind == "editable" and not mode.updatable


@pytest.mark.parametrize("prefix, base, kind", [
    ("/home/u/.local/share/uv/tools/agflow", "/usr", "uv-tool"),
    ("/home/u/venv", "/usr", "venv"),
    ("/usr", "/usr", "system"),
])
def test_install_kinds(tmp_path, monkeypatch, prefix, base, kind):
    (tmp_path / "site" / "agflow").mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", prefix)
    monkeypatch.setattr(sys, "base_prefix", base)
    mode = update.detect_install_mode(tmp_path / "site" / "agflow", "py")
    assert mode.kind == kind and mode.updatable and mode.python == "py" and mode.site_dir == tmp_path / "site"


# --- the installer command ----------------------------------------------------------------------


def test_installer_prefers_uv_and_keeps_the_sdk_extra(site, mode, monkeypatch):
    monkeypatch.setattr(update.shutil, "which", lambda name: "/bin/uv")
    wheel = site / "agflow-0.4.0-py3-none-any.whl"
    plain = real_installer_argv(wheel, mode, sdk=False)
    assert plain == ["/bin/uv", "pip", "install", "--quiet", "--python", sys.executable, str(wheel)]
    with_sdk = real_installer_argv(wheel, mode, sdk=True, reinstall=True)
    assert "--reinstall-package" in with_sdk and with_sdk[-1] == f"agflow[sdk] @ {wheel.as_uri()}"


def test_installer_falls_back_to_pip_and_gives_up_without_either(site, mode, monkeypatch):
    monkeypatch.setattr(update.shutil, "which", lambda name: None)
    monkeypatch.setattr(update.importlib.util, "find_spec", lambda name: object())
    argv = real_installer_argv(site / "x.whl", mode, sdk=False, reinstall=True)
    assert argv[1:4] == ["-m", "pip", "install"] and "--force-reinstall" in argv
    monkeypatch.setattr(update.importlib.util, "find_spec", lambda name: None)
    assert real_installer_argv(site / "x.whl", mode, sdk=False) is None


# --- the plan -----------------------------------------------------------------------------------


def make_plan(gh, tmp_path, mode, *, current="0.3.0", agy="1.3.3", python=(3, 12, 0), config=None, **kw):
    return update.plan(feed=feed(gh), directory=tmp_path, config=config, current=current, mode=mode,
                       snapshot=ap.Snapshot(agy_version=agy), python=python, **kw)


GOOD_META = {"version": "0.4.0", "min_antigravity": "1.3.0", "tested_antigravity": ["1.3.3"], "python": ">=3.10"}


@pytest.fixture(autouse=True)
def an_installer_exists(monkeypatch):
    monkeypatch.setattr(update, "installer_argv", lambda *a, **k: ["fake-install"])


def test_a_healthy_update_is_planned_with_its_steps_and_notes(gh, tmp_path, mode):
    gh.add_release("0.4.0", meta=GOOD_META, notes="\x1b[1mWhat is new\x1b[0m")
    plan = make_plan(gh, tmp_path, mode)
    assert plan.ok and plan.target == "0.4.0" and plan.bump == "minor" and plan.warnings == []
    assert plan.notes == "What is new" and plan.wheel_name.endswith(".whl") and len(plan.steps) == 6
    assert "blocked" not in "\n".join(plan.lines()) and plan.to_json()["ok"] is True


def test_the_installed_version_cannot_be_installed_again_unless_asked(gh, tmp_path, mode):
    gh.add_release("0.4.0", meta=GOOD_META)
    assert "already installed" in make_plan(gh, tmp_path, mode, current="0.4.0").blocked[0]
    assert make_plan(gh, tmp_path, mode, current="0.4.0", reinstall=True).ok


def test_updates_only_move_forward(gh, tmp_path, mode):
    gh.add_release("0.3.0", meta=GOOD_META)
    gh.add_release("0.4.0", meta=GOOD_META)
    plan = make_plan(gh, tmp_path, mode, current="0.4.0", target="0.3.0")
    assert not plan.ok and "older than the installed" in plan.blocked[0]


def test_a_specific_release_can_be_chosen_and_an_unknown_one_is_refused(gh, tmp_path, mode):
    gh.add_release("0.4.0", meta=GOOD_META)
    gh.add_release("0.4.1", meta=GOOD_META)
    assert make_plan(gh, tmp_path, mode, target="0.4.0").target == "0.4.0"
    with pytest.raises(rf.FeedError, match="not an installable release"):
        make_plan(gh, tmp_path, mode, target="9.9.9")


def test_nothing_published_is_an_error_with_a_clear_message(gh, tmp_path, mode):
    with pytest.raises(rf.FeedError, match="no installable release"):
        make_plan(gh, tmp_path, mode)


def test_a_development_install_is_blocked(gh, tmp_path, site):
    gh.add_release("0.4.0", meta=GOOD_META)
    dev = update.InstallMode("editable", site / "agflow", site, sys.executable)
    plan = make_plan(gh, tmp_path, dev)
    assert not plan.ok and "development install" in plan.blocked[0]


def test_an_unwritable_environment_is_blocked(gh, tmp_path, mode, monkeypatch):
    gh.add_release("0.4.0", meta=GOOD_META)
    monkeypatch.setattr(update.os, "access", lambda path, flag: False)
    assert "not writable" in make_plan(gh, tmp_path, mode).blocked[0]


def test_no_way_to_install_is_blocked(gh, tmp_path, mode, monkeypatch):
    gh.add_release("0.4.0", meta=GOOD_META)
    monkeypatch.setattr(update, "installer_argv", lambda *a, **k: None)
    assert "neither `uv` nor `pip`" in make_plan(gh, tmp_path, mode).blocked[0]


def test_a_running_or_unfinished_update_blocks_a_new_one(gh, tmp_path, mode):
    gh.add_release("0.4.0", meta=GOOD_META)
    update.lock_path(tmp_path).write_text("1 1\n", encoding="utf-8")
    assert "another update is running" in make_plan(gh, tmp_path, mode).blocked[0]
    old = 1_000_000_000
    os.utime(update.lock_path(tmp_path), (old, old))   # a crashed update's leftover is not a running update
    assert make_plan(gh, tmp_path, mode).ok
    update.journal_path(tmp_path).write_text('{"stage": "package-installed"}', encoding="utf-8")
    assert "did not finish" in make_plan(gh, tmp_path, mode).blocked[0]
    update.journal_path(tmp_path).write_text('{"stage": "done"}', encoding="utf-8")
    assert make_plan(gh, tmp_path, mode).ok


def test_an_antigravity_older_than_the_release_needs_blocks_and_is_never_updated_by_agflow(gh, tmp_path, mode):
    gh.add_release("0.4.0", meta=dict(GOOD_META, min_antigravity="1.4.0"))
    plan = make_plan(gh, tmp_path, mode, agy="1.3.3")
    assert not plan.ok and "agy update" in plan.blocked[0] and "never updates it" in plan.blocked[0]
    assert make_plan(gh, tmp_path, mode, agy="1.4.0").ok
    assert make_plan(gh, tmp_path, mode, agy=None).ok, "an Antigravity that cannot be read does not block"


def test_an_untested_antigravity_is_a_warning_not_a_block(gh, tmp_path, mode):
    gh.add_release("0.4.0", meta=GOOD_META)
    plan = make_plan(gh, tmp_path, mode, agy="1.9.0")
    assert plan.ok and "not tested against your Antigravity 1.9.0" in plan.warnings[0]


def test_an_unsupported_python_blocks(gh, tmp_path, mode):
    gh.add_release("0.4.0", meta=dict(GOOD_META, python=">=3.13"))
    assert "needs Python >=3.13" in make_plan(gh, tmp_path, mode, python=(3, 12, 1)).blocked[0]
    assert make_plan(gh, tmp_path, mode, python=(3, 13, 0)).ok


def test_breaking_and_major_releases_need_an_explicit_go_ahead(gh, tmp_path, mode):
    gh.add_release("0.4.0", meta=dict(GOOD_META, breaking=True))
    assert "breaking changes" in make_plan(gh, tmp_path, mode).blocked[0]
    allowed = make_plan(gh, tmp_path, mode, allow_breaking=True)
    assert allowed.ok and "--allow-breaking" in allowed.warnings[0]
    gh.add_release("1.0.0", meta=dict(GOOD_META))
    assert "major version" in make_plan(gh, tmp_path, mode).blocked[0]


def test_a_release_without_or_with_a_damaged_compat_file_still_plans(gh, tmp_path, mode):
    gh.add_release("0.4.0")
    plan = make_plan(gh, tmp_path, mode)
    assert plan.ok and "does not declare" in plan.warnings[0]
    gh.releases[0]["assets"].append({"name": "release.json", "size": 3, "browser_download_url":
                                     gh.releases[0]["assets"][0]["browser_download_url"].rsplit("/", 1)[0]
                                     + "/missing.json"})
    plan = make_plan(gh, tmp_path, mode)
    assert plan.ok and "could not be read" in plan.warnings[0]


def test_the_channel_decides_whether_a_release_candidate_is_offered(gh, tmp_path, mode):
    gh.add_release("0.4.0", meta=GOOD_META)
    gh.add_release("0.5.0rc1", prerelease=True, meta=dict(GOOD_META, version="0.5.0rc1"))
    assert make_plan(gh, tmp_path, mode).target == "0.4.0"
    plan = make_plan(gh, tmp_path, mode, config=update.Config(channel="pre"))
    assert plan.target == "0.5.0rc1" and any("pre-release" in w for w in plan.warnings)


def test_release_notes_in_a_plan_are_display_safe(gh, tmp_path, mode):
    gh.add_release("0.4.0", meta=dict(GOOD_META, notes="\x1b[2Jbe careful"), notes="\x1b]0;x\x07ok")
    plan = make_plan(gh, tmp_path, mode)
    assert plan.notes == "ok" and all("\x1b" not in line for line in plan.lines())
