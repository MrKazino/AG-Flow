"""Applying an update and undoing it: verified download, inspected wheel, backup, install, refresh with the user's edits
kept, verification - and an exact restore on any failure or crash.

The plugin install and the edit merge are real (sandboxed HOME); the Python package swap and the subprocess calls are
played by tests/fake_update_env.py."""

from __future__ import annotations

import io
import json
import os
import stat
import sys
import zipfile
from pathlib import Path

import pytest

from agflow import antigravity_probe as ap
from agflow import install as inst
from agflow import paths, plugin_files, update, update_apply
from agflow import release_feed as rf
from tests.fake_github import REPO, FakeGitHub
from tests.fake_update_env import FakeEnv, global_state, tree

CONFLICT, KEPT, REMOVED = "tdd", "docs-writer", "perf-profile"   # three bundled skills
META = {"version": "0.4.0", "min_antigravity": "1.3.0", "tested_antigravity": ["1.3.3"], "python": ">=3.10"}
NEW_SKILL = (b"---\nname: shiny\ndescription: A new upstream skill. Use when testing updates.\n---\n\n# Shiny\n")


def upstream_changes(files: dict[str, bytes]) -> None:
    prefix = "agflow/_plugin/skills/"
    files[f"{prefix}{CONFLICT}/SKILL.md"] += b"\nUpstream addition.\n"
    files[f"{prefix}shiny/SKILL.md"] = NEW_SKILL
    for key in [k for k in files if k.startswith(f"{prefix}{REMOVED}/")]:
        del files[key]


@pytest.fixture
def gh():
    fake = FakeGitHub().start()
    yield fake
    fake.stop()


@pytest.fixture
def env(tmp_path, plugin_dir, monkeypatch):
    monkeypatch.setattr(update_apply, "installer_argv", lambda wheel, mode, **kw: ["fake-install", str(wheel)])
    monkeypatch.setattr(update, "installer_argv", lambda *a, **k: ["fake-install"])
    monkeypatch.setattr(ap, "sdk_version", lambda: None)
    fake = FakeEnv(tmp_path / "env", plugin_dir)
    inst.install("global", None, source=fake.bundle)
    return fake


@pytest.fixture
def state(tmp_path) -> Path:
    return tmp_path / "state"


def feed(gh) -> rf.Feed:
    return rf.Feed(REPO, api=gh.base, allow_loopback=True, token=None)


def publish(gh, env, version="0.4.0", mutate=upstream_changes, meta=None, **kw):
    return gh.add_release(version, wheel=env.make_wheel(version, mutate=mutate),
                          meta=dict(META, version=version) if meta is None else meta, **kw)


def make_plan(env, gh, state, **kw):
    return update.plan(feed=feed(gh), directory=state, current="0.3.0", mode=env.mode, python=(3, 12, 0),
                       snapshot=ap.Snapshot(agy_version="1.3.3"), **kw)


def do_apply(env, gh, state, plan=None, **kw):
    plan = plan or make_plan(env, gh, state)
    return update_apply.apply(plan, feed=feed(gh), directory=state, run=env.runner(), bundle=lambda: env.bundle, **kw)


def plugin_root() -> Path:
    return paths.global_layout().plugins_dir / "agflow"


def user_edits() -> None:
    root = plugin_root()
    for name in (CONFLICT, KEPT):
        with (root / "skills" / name / "SKILL.md").open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(f"\nMY EDIT of {name}\n")
    (root / "skills" / "mine").mkdir()
    (root / "skills" / "mine" / "SKILL.md").write_text("---\nname: mine\ndescription: Mine. Use when mine.\n---\n",
                                                       encoding="utf-8")


# --- the whole update -----------------------------------------------------------------------------


def test_a_full_update_swaps_the_package_and_refreshes_the_plugin_keeping_the_users_edits(env, gh, state):
    user_edits()
    publish(gh, env)
    messages: list[str] = []
    outcome = do_apply(env, gh, state, log=messages.append)

    assert outcome.ok and not outcome.problems and not outcome.rolled_back
    assert env.installed_version() == "0.4.0"
    assert (env.site / "agflow" / "module.py").read_text() == "VALUE = 'new 0.4.0'\n"
    assert [p.name for p in env.site.glob("agflow-*.dist-info")] == ["agflow-0.4.0.dist-info"]
    skills = plugin_root() / "skills"
    assert "MY EDIT of docs-writer" in (skills / KEPT / "SKILL.md").read_text(encoding="utf-8")
    assert "MY EDIT of tdd" in (skills / CONFLICT / "SKILL.md").read_text(encoding="utf-8")
    assert "Upstream addition" not in (skills / CONFLICT / "SKILL.md").read_text(encoding="utf-8"), "you win conflicts"
    saved = state / "incoming" / "0.4.0-global" / "skills" / CONFLICT / "SKILL.md"
    assert "Upstream addition" in saved.read_text(encoding="utf-8"), "the new version is kept for merging"
    assert (skills / "mine" / "SKILL.md").is_file() and (skills / "shiny" / "SKILL.md").read_bytes() == NEW_SKILL
    assert not (skills / REMOVED).exists()
    assert any("kept your edit of skills/docs-writer" in line for line in outcome.merge["global"])
    assert messages == outcome.log and "checksum verified (SHA256SUMS)" in outcome.log[1]


def test_the_install_record_describes_what_was_installed_not_the_users_edits(env, gh, state):
    user_edits()
    publish(gh, env)
    do_apply(env, gh, state)
    recorded = plugin_files.load(plugin_files.record_path(inst.manifest_path("global", None)))
    upstream = plugin_files.pristine(env.bundle)
    kept = f"skills/{KEPT}/SKILL.md"
    assert recorded[kept] == upstream[kept] != plugin_files.fingerprint(plugin_root())[kept]
    assert "skills/shiny/SKILL.md" in recorded and f"skills/{REMOVED}/SKILL.md" not in recorded
    changed = plugin_files.changes(plugin_root(), recorded)
    assert kept in changed.modified and "skills/mine/SKILL.md" in changed.added, "your edits stay detectable"


def test_the_steps_run_in_a_safe_order(env, gh, state):
    publish(gh, env)
    do_apply(env, gh, state)
    kinds = ["doctor" if "doctor" in c else "install" if c[0] == "fake-install" else c[4] for c in env.calls]
    assert kinds == ["doctor", "install", "refresh", "verify"]
    assert env.calls[0][-1] == "--json" and "--path" in env.calls[0]


def test_bookkeeping_after_a_success(env, gh, state):
    publish(gh, env)
    outcome = do_apply(env, gh, state)
    journal = update_apply.load_journal(state)
    assert journal["stage"] == "done" and journal["from"] == "0.3.0" and journal["to"] == "0.4.0"
    assert update.unfinished_journal(state) is None and not update.lock_path(state).exists()
    assert not update_apply.work_root(state).exists(), "the scratch folder is removed"
    assert update_apply.work_root(state) != paths.install_tmp_dir(state), "the installer empties install-tmp"
    assert (Path(journal["backup_dir"]) / "pkg" / "agflow" / "module.py").is_file()
    last = update.load_state(state)["last_apply"]
    assert last["ok"] is True and last["to"] == "0.4.0" and outcome.to_json()["ok"] is True


# --- undoing it -----------------------------------------------------------------------------------


def test_an_update_can_be_rolled_back_afterwards_and_everything_returns_to_how_it_was(env, gh, state):
    user_edits()
    before_pkg, before_state = env.package_tree(), global_state()
    publish(gh, env)
    assert do_apply(env, gh, state).ok
    assert env.package_tree() != before_pkg
    outcome = update_apply.rollback(directory=state, run=env.runner(), mode=env.mode)
    assert outcome.ok and outcome.rolled_back and (outcome.from_version, outcome.to_version) == ("0.4.0", "0.3.0")
    assert env.package_tree() == before_pkg and global_state() == before_state
    assert env.installed_version() == "0.3.0"
    with pytest.raises(update_apply.UpdateError, match="already been rolled back"):
        update_apply.rollback(directory=state, run=env.runner(), mode=env.mode)


def test_rollback_with_nothing_to_roll_back(env, state):
    with pytest.raises(update_apply.UpdateError, match="no update to roll back"):
        update_apply.rollback(directory=state, run=env.runner(), mode=env.mode)


def test_rollback_refuses_when_the_backup_is_gone(env, gh, state):
    publish(gh, env)
    do_apply(env, gh, state)
    journal = update_apply.load_journal(state)
    import shutil
    shutil.rmtree(Path(journal["backup_dir"]))
    with pytest.raises(update_apply.UpdateError, match="backup of the last update is gone"):
        update_apply.rollback(directory=state, run=env.runner(), mode=env.mode)


FAILURES = [
    ("install-fails", lambda e: setattr(e, "install_behaviour", "fail"), "installing the new version failed"),
    ("install-half-done", lambda e: setattr(e, "install_behaviour", "corrupt-then-fail"),
     "installing the new version failed"),
    ("refresh-fails", lambda e: setattr(e, "refresh_fails", True), "refreshing the global plugin failed"),
    ("new-version-unhealthy", lambda e: setattr(e, "verify_fails_for", {"0.4.0"}), "fails its own checks"),
    ("wrong-version-running", lambda e: setattr(e, "verify_version", "0.3.0"), "runs AG-Flow 0.3.0, not 0.4.0"),
    ("unexpected-error", lambda e: setattr(e, "raise_at", ("refresh", ValueError("surprise"))), "unexpected error"),
]


@pytest.mark.parametrize("tweak, message", [pytest.param(t, m, id=i) for i, t, m in FAILURES])
def test_any_failure_after_the_backup_restores_everything_exactly(env, gh, state, tweak, message):
    user_edits()
    publish(gh, env)
    before_pkg, before_state = env.package_tree(), global_state()
    tweak(env)
    outcome = do_apply(env, gh, state)
    assert not outcome.ok and outcome.rolled_back and message in outcome.problems[0]
    assert env.package_tree() == before_pkg, "the installed package is exactly as it was"
    assert global_state() == before_state, "the plugin, manifests and GEMINI.md are exactly as they were"
    assert env.installed_version() == "0.3.0"
    assert update_apply.load_journal(state)["stage"] == "rolled-back" and update.unfinished_journal(state) is None
    assert not update.lock_path(state).exists()
    last = update.load_state(state)["last_apply"]
    assert last["ok"] is False and last["rolled_back"] is True and last["problems"]


def test_when_the_restored_version_also_fails_its_check_the_user_is_told_plainly(env, gh, state):
    publish(gh, env)
    env.install_behaviour = "fail"
    env.verify_fails_for = {"0.3.0"}
    outcome = do_apply(env, gh, state)
    assert outcome.rolled_back and any("restored version did not pass its check" in p for p in outcome.problems)
    assert any("agflow doctor" in p for p in outcome.problems)


def test_a_failed_rollback_says_where_the_backup_is(env, gh, state, monkeypatch):
    publish(gh, env)
    env.install_behaviour = "fail"

    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(update_apply, "_restore_package", broken)
    outcome = do_apply(env, gh, state)
    assert not outcome.rolled_back and "automatic rollback failed (disk full)" in outcome.problems[-1]
    assert str(update_apply.load_journal(state)["backup_dir"]) in outcome.problems[-1]


def test_a_crash_leaves_a_journal_and_rollback_finishes_the_job(env, gh, state):
    user_edits()
    publish(gh, env)
    before_pkg, before_state = env.package_tree(), global_state()
    env.raise_at = ("refresh", KeyboardInterrupt())   # the process is killed half way
    with pytest.raises(KeyboardInterrupt):
        do_apply(env, gh, state)
    assert env.installed_version() == "0.4.0", "the package was already swapped when it died"
    assert not update.lock_path(state).exists(), "the lock is released even when the process is interrupted"
    assert update.unfinished_journal(state)["stage"] == "package-installed"
    plan = make_plan(env, gh, state)
    assert "did not finish" in plan.blocked[0]
    env.raise_at = None
    outcome = update_apply.rollback(directory=state, run=env.runner(), mode=env.mode)
    assert outcome.ok and env.package_tree() == before_pkg and global_state() == before_state
    assert update.unfinished_journal(state) is None


# --- nothing is changed until the new version has proven itself -----------------------------------


def untouched(env, before_pkg, before_state):
    assert env.package_tree() == before_pkg and global_state() == before_state
    assert not any("fake-install" in c[0] for c in env.calls)


def early_failure(env, gh, state, fragment):
    before_pkg, before_state = env.package_tree(), global_state()
    outcome = do_apply(env, gh, state)
    assert not outcome.ok and not outcome.rolled_back and fragment in outcome.problems[0]
    untouched(env, before_pkg, before_state)
    assert update.unfinished_journal(state) is None and not update.lock_path(state).exists()
    return outcome


def test_a_tampered_download_is_refused_before_anything_changes(env, gh, state):
    publish(gh, env, tamper_sums=True)
    early_failure(env, gh, state, "does not match SHA256SUMS")


def test_a_download_that_disagrees_with_githubs_own_digest_is_refused(env, gh, state):
    rel = publish(gh, env, digest=True)
    next(a for a in rel["assets"] if a["name"].endswith(".whl"))["digest"] = "sha256:" + "0" * 64
    early_failure(env, gh, state, "digest GitHub reports")


def test_a_wheel_missing_from_the_checksum_list_is_refused(env, gh, state):
    rel = publish(gh, env)
    sums = next(a for a in rel["assets"] if a["name"] == "SHA256SUMS")
    gh.files[sums["browser_download_url"].replace(gh.base, "")] = b"0" * 64 + b"  some-other-file.whl\n"
    early_failure(env, gh, state, "does not list")


def test_a_new_version_that_fails_doctor_on_its_own_plugin_is_never_installed(env, gh, state):
    publish(gh, env)
    env.doctor_errors = 2
    early_failure(env, gh, state, "has 2 error(s) in doctor")


def test_a_new_version_that_cannot_even_start_is_refused(env, gh, state):
    publish(gh, env)
    runner = env.runner()

    def crashing(argv, *a, **k):
        env.calls.append(list(argv))
        if "doctor" in argv:
            return update_apply.Proc(1, "", "Traceback ...\nSyntaxError: invalid syntax")
        return runner(argv, *a, **k)

    outcome = update_apply.apply(make_plan(env, gh, state), feed=feed(gh), directory=state, run=crashing,
                                 bundle=lambda: env.bundle)
    assert not outcome.ok and "could not check its own plugin" in outcome.problems[0]
    assert "SyntaxError: invalid syntax" in outcome.problems[0]
    assert env.installed_version() == "0.3.0"


def test_missing_dependencies_in_the_scratch_check_are_not_a_reason_to_stop(env, gh, state):
    publish(gh, env)
    runner = env.runner()

    def lacking(argv, *a, **k):
        if "doctor" in argv:
            return update_apply.Proc(1, "", "ModuleNotFoundError: No module named 'brand_new_dependency'")
        return runner(argv, *a, **k)

    outcome = update_apply.apply(make_plan(env, gh, state), feed=feed(gh), directory=state, run=lacking,
                                 bundle=lambda: env.bundle)
    assert outcome.ok and any("needs packages this environment lacks" in m for m in outcome.log)


def test_a_blocked_plan_is_not_applied(env, gh, state):
    publish(gh, env, meta=dict(META, min_antigravity="9.0.0"))
    plan = make_plan(env, gh, state)
    assert not plan.ok
    with pytest.raises(update_apply.UpdateError, match="needs Antigravity 9.0.0"):
        update_apply.apply(plan, feed=feed(gh), directory=state, run=env.runner(), bundle=lambda: env.bundle)
    assert env.calls == []


def test_a_second_update_cannot_start_while_one_runs(env, gh, state):
    publish(gh, env)
    plan = make_plan(env, gh, state)
    state.mkdir(parents=True, exist_ok=True)
    update.lock_path(state).write_text("1 1\n", encoding="utf-8")
    with pytest.raises(update_apply.UpdateError, match="another update is running"):
        update_apply.apply(plan, feed=feed(gh), directory=state, run=env.runner(), bundle=lambda: env.bundle)
    assert update.lock_path(state).exists(), "somebody else's lock is not ours to remove"
    old = 1_000_000_000
    os.utime(update.lock_path(state), (old, old))   # a crashed update's leftover is taken over
    assert update_apply.apply(plan, feed=feed(gh), directory=state, run=env.runner(), bundle=lambda: env.bundle).ok


# --- what is installed is refreshed the way it was installed --------------------------------------


def refresh_calls(env) -> list[list[str]]:
    return [c for c in env.calls if c[3:5] == ["update", "refresh"]]


def test_a_profile_install_keeps_its_profile(tmp_path, plugin_dir, gh, state, monkeypatch):
    monkeypatch.setattr(update_apply, "installer_argv", lambda wheel, mode, **kw: ["fake-install", str(wheel)])
    monkeypatch.setattr(update, "installer_argv", lambda *a, **k: ["fake-install"])
    monkeypatch.setattr(ap, "sdk_version", lambda: None)
    env = FakeEnv(tmp_path / "env", plugin_dir)
    inst.install("global", None, source=env.bundle, profile="core", with_hooks=False, with_gemini_md=False)
    before_skills = sorted(p.name for p in (plugin_root() / "skills").iterdir())
    publish(gh, env, mutate=None)
    assert do_apply(env, gh, state).ok
    [call] = refresh_calls(env)
    assert call[call.index("--profile") + 1] == "core" and "--no-hooks" in call and "--no-gemini-md" in call
    assert sorted(p.name for p in (plugin_root() / "skills").iterdir()) == before_skills


def test_global_and_workspace_installs_are_both_refreshed(env, gh, state, workspace):
    inst.install("workspace", workspace, source=env.bundle, allow_duplicate=True)
    publish(gh, env)
    outcome = update_apply.apply(make_plan(env, gh, state), feed=feed(gh), directory=state, workspace=workspace,
                                 run=env.runner(), bundle=lambda: env.bundle)
    assert outcome.ok and set(outcome.merge) == {"global", "workspace"}
    scopes = {c[c.index("--scope") + 1]: c for c in refresh_calls(env)}
    assert set(scopes) == {"global", "workspace"} and "--allow-duplicate" in scopes["global"]
    assert scopes["workspace"][scopes["workspace"].index("--workspace-root") + 1] == str(workspace)
    assert (workspace / ".agents" / "plugins" / "agflow" / "skills" / "shiny" / "SKILL.md").is_file()


def test_a_failure_in_the_second_install_undoes_the_first_too(env, gh, state, workspace):
    inst.install("workspace", workspace, source=env.bundle, allow_duplicate=True)
    publish(gh, env)
    before_pkg, before_state = env.package_tree(), global_state()
    before_ws = tree(workspace / ".agents")
    runner = env.runner()

    def second_fails(argv, *a, **k):
        if argv[3:5] == ["update", "refresh"] and "workspace" in argv:
            return update_apply.Proc(1, "", "error: workspace refused")
        return runner(argv, *a, **k)

    outcome = update_apply.apply(make_plan(env, gh, state), feed=feed(gh), directory=state, workspace=workspace,
                                 run=second_fails, bundle=lambda: env.bundle)
    assert not outcome.ok and outcome.rolled_back and "workspace plugin failed" in outcome.problems[0]
    assert env.package_tree() == before_pkg and global_state() == before_state
    assert tree(workspace / ".agents") == before_ws


def test_a_linked_development_install_follows_its_source_and_is_left_alone(tmp_path, plugin_dir, gh, state,
                                                                          monkeypatch, require_symlinks):
    monkeypatch.setattr(update_apply, "installer_argv", lambda wheel, mode, **kw: ["fake-install", str(wheel)])
    monkeypatch.setattr(update, "installer_argv", lambda *a, **k: ["fake-install"])
    monkeypatch.setattr(ap, "sdk_version", lambda: None)
    env = FakeEnv(tmp_path / "env", plugin_dir)
    inst.install("global", None, source=env.bundle, link=True)
    publish(gh, env)
    assert do_apply(env, gh, state).ok
    assert refresh_calls(env) == [] and plugin_root().is_symlink()


def test_the_sdk_extra_and_a_repair_install_are_passed_to_the_installer(env, gh, state, monkeypatch):
    seen = {}
    monkeypatch.setattr(ap, "sdk_version", lambda: "0.1.20")
    monkeypatch.setattr(update_apply, "installer_argv",
                        lambda wheel, mode, **kw: seen.update(kw) or ["fake-install", str(wheel)])
    publish(gh, env)
    assert do_apply(env, gh, state).ok and seen == {"sdk": True, "reinstall": False}


def test_a_repair_install_of_the_same_version(env, gh, state):
    publish(gh, env, "0.3.0", mutate=None)
    plan = update.plan(feed=feed(gh), directory=state, current="0.3.0", mode=env.mode, reinstall=True,
                       python=(3, 12, 0), snapshot=ap.Snapshot(agy_version="1.3.3"))
    assert plan.ok and plan.reinstall
    assert update_apply.apply(plan, feed=feed(gh), directory=state, run=env.runner(), bundle=lambda: env.bundle).ok


# --- the wheel inspector --------------------------------------------------------------------------


def wheel_with(tmp_path, mutate=None, **kw) -> Path:
    env_dir = tmp_path / "w"
    env_dir.mkdir(exist_ok=True)
    fake = FakeEnv(env_dir / "e", Path(__file__).resolve().parent.parent / "plugin")
    path = env_dir / "agflow-0.4.0-py3-none-any.whl"
    path.write_bytes(fake.make_wheel("0.4.0", mutate=mutate, **kw))
    return path


def zip_with(path: Path, entries: dict[str, bytes], infos: dict[str, zipfile.ZipInfo] | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr((infos or {}).get(name, name), data)
    return path


GOOD = {"agflow/__init__.py": b'__version__ = "0.4.0"\n',
        "agflow-0.4.0.dist-info/METADATA": b"Name: agflow\nVersion: 0.4.0\n"}


def test_a_genuine_wheel_passes_inspection(tmp_path):
    update_apply.inspect_wheel(wheel_with(tmp_path), "0.4.0")
    update_apply.inspect_wheel(zip_with(tmp_path / "ok.whl", GOOD), "0.4.0")


@pytest.mark.parametrize("entries, fragment", [
    ({**GOOD, "evil/x.py": b""}, "'evil', which is not AG-Flow's"),
    ({**GOOD, "agflow/../../evil.py": b""}, "unsafe entry"),
    ({**GOOD, "/etc/passwd": b""}, "unsafe entry"),
    pytest.param({**GOOD, "agflow\\sneaky.py": b""}, "unsafe entry", marks=pytest.mark.skipif(
        os.name == "nt", reason="zip entry names are normalised to '/' on Windows: the case cannot occur there")),
    ({**GOOD, "C:evil.py": b""}, "unsafe entry"),
    ({"agflow/__init__.py": b'__version__ = "0.4.0"\n'}, "no metadata"),
    ({**GOOD, "agflow-0.4.0.dist-info/METADATA": b"Name: other\nVersion: 0.4.0\n"}, "not AG-Flow's"),
    ({**GOOD, "agflow-0.4.0.dist-info/METADATA": b"Name: agflow\nVersion: 0.4.1\n"}, "is not 0.4.0"),
    ({**GOOD, "agflow-0.4.0.dist-info/METADATA": b"Name: agflow\n"}, "is not 0.4.0"),
    ({**GOOD, "agflow/__init__.py": b'__version__ = "0.3.0"\n'}, "different version than its metadata"),
    ({**GOOD, "agflow/__init__.py": b"# no version\n"}, "different version than its metadata"),
    ({"agflow-0.4.0.dist-info/METADATA": GOOD["agflow-0.4.0.dist-info/METADATA"]}, "no agflow package"),
])
def test_wheels_that_are_not_exactly_agflows_are_refused(tmp_path, entries, fragment):
    with pytest.raises(update_apply.UpdateError, match=fragment):
        update_apply.inspect_wheel(zip_with(tmp_path / "bad.whl", entries), "0.4.0")


def test_a_wheel_with_a_symlink_entry_is_refused(tmp_path):
    link = zipfile.ZipInfo("agflow/link")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(update_apply.UpdateError, match="unsafe entry"):
        wheel = zip_with(tmp_path / "l.whl", {**GOOD, "agflow/link": b"/etc"}, {"agflow/link": link})
        update_apply.inspect_wheel(wheel, "0.4.0")


def test_not_a_zip_and_zip_bombs_are_refused(tmp_path, monkeypatch):
    junk = tmp_path / "junk.whl"
    junk.write_bytes(b"this is not a zip file")
    with pytest.raises(update_apply.UpdateError, match="not a readable wheel"):
        update_apply.inspect_wheel(junk, "0.4.0")
    many = {f"agflow/f{i}.py": b"" for i in range(update_apply.MAX_WHEEL_FILES + 1)}
    with pytest.raises(update_apply.UpdateError, match="unreasonably large"):
        update_apply.inspect_wheel(zip_with(tmp_path / "many.whl", {**GOOD, **many}), "0.4.0")
    monkeypatch.setattr(update_apply, "MAX_WHEEL_UNPACKED", 10)
    with pytest.raises(update_apply.UpdateError, match="unreasonably large"):
        update_apply.inspect_wheel(zip_with(tmp_path / "big.whl", {**GOOD, "agflow/big.bin": b"x" * 100}), "0.4.0")


def test_an_unparseable_expected_version_is_refused(tmp_path):
    with pytest.raises(update_apply.UpdateError, match="is not"):
        update_apply.inspect_wheel(zip_with(tmp_path / "ok.whl", GOOD), "not-a-version")


def test_extraction_stays_inside_the_scratch_folder(tmp_path):
    good = zip_with(tmp_path / "ok.whl", GOOD)
    update_apply._extract(good, tmp_path / "out")
    assert (tmp_path / "out" / "agflow" / "__init__.py").is_file()
    evil = zip_with(tmp_path / "evil.whl", {**GOOD, "../escape.txt": b"x"})
    with pytest.raises(update_apply.UpdateError, match="unsafe entry"):
        update_apply._extract(evil, tmp_path / "out2")
    assert not (tmp_path / "escape.txt").exists()


# --- small pieces ---------------------------------------------------------------------------------


@pytest.mark.parametrize("text, expected", [
    ('{"a": 1}', {"a": 1}), ('noise\n{"a": 2}\n', {"a": 2}), ('{"a": 1}\nmore noise\n{"b": 3}', {"b": 3}),
    ('{\n  "a": 4\n}', {"a": 4}), ("[1, 2]", None), ("not json", None), ("", None), ("{broken\n", None),
])
def test_json_from(text, expected):
    assert update_apply._json_from(text) == expected


def test_run_command_never_raises(tmp_path):
    ok = update_apply.run_command([sys.executable, "-c", "print('hi')"])
    assert (ok.returncode, ok.stdout.strip()) == (0, "hi")
    assert update_apply.run_command([str(tmp_path / "no-such-program")]).returncode == 127
    slow = update_apply.run_command([sys.executable, "-c", "import threading; threading.Event().wait(30)"], timeout=0.3)
    assert slow.returncode == 124 and "timed out" in slow.stderr
    odd = update_apply.run_command([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xff\\xfe ok')"])
    assert odd.returncode == 0 and "ok" in odd.stdout


def test_only_the_newest_backups_are_kept(tmp_path):
    root = tmp_path / "backups"
    for i in range(8):
        (root / f"2026010{i}-000000-update").mkdir(parents=True)
    (root / "unrelated").mkdir()
    update_apply._prune_backups(root)
    left = sorted(p.name for p in root.iterdir())
    assert left == ["20260103-000000-update", "20260104-000000-update", "20260105-000000-update",
                    "20260106-000000-update", "20260107-000000-update", "unrelated"]


def test_everything_needed_after_the_swap_is_importable_up_front():
    update_apply._preload()
    for name in update_apply._PRELOAD:
        assert name in sys.modules


def test_a_progress_line_for_every_stage_reaches_the_caller(env, gh, state):
    publish(gh, env)
    seen: list[str] = []
    do_apply(env, gh, state, log=seen.append)
    assert [m.split(" ")[0] for m in seen][:2] == ["downloaded", "checksum"]
    assert any(m.startswith("backed up") for m in seen) and any(m.startswith("installed AG-Flow") for m in seen)
    assert seen[-1] == "the new version passes doctor and the hook probe"
    assert json.dumps(seen)
    assert io.StringIO is not None
