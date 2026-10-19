"""Which files of an installed plugin are still what agflow installed, and what an update does with your changes."""

from __future__ import annotations

import json
from pathlib import Path

from agflow import plugin_files as pf
from agflow.install import install
from tests.conftest import write


def make_bundle(root: Path, **files: str) -> Path:
    for rel, text in files.items():
        write(root / rel.replace("__", "/"), text)
    return root


BASE = {"plugin.json": "{}", "skills__tdd__SKILL.md": "tdd v1\n", "skills__docs__SKILL.md": "docs v1\n",
        "graphs__bugfix.yaml": "id: bugfix\n", "hooks.json": "{wired}", "mcp_config.json": "{wired}"}


def test_fingerprint_hashes_content_with_unix_newlines_and_skips_noise(tmp_path):
    root = make_bundle(tmp_path / "p", **BASE)
    write(root / "skills" / "tdd" / "__pycache__" / "x.pyc", "junk")
    write(root / ".DS_Store", "junk")
    fp = pf.fingerprint(root)
    assert set(fp) == {"plugin.json", "skills/tdd/SKILL.md", "skills/docs/SKILL.md", "graphs/bugfix.yaml",
                       "hooks.json", "mcp_config.json"}
    crlf = tmp_path / "q"
    write(crlf / "skills" / "tdd" / "SKILL.md", "x")
    (crlf / "skills" / "tdd" / "SKILL.md").write_bytes(b"tdd v1\r\n")
    assert pf.fingerprint(crlf)["skills/tdd/SKILL.md"] == fp["skills/tdd/SKILL.md"], "autocrlf is not an edit"


def test_wired_files_are_managed_and_never_counted_as_user_edits(tmp_path):
    root = make_bundle(tmp_path / "p", **BASE)
    recorded = pf.pristine(root)
    assert "hooks.json" not in recorded and "mcp_config.json" not in recorded
    write(root / "hooks.json", "{rewired for this machine}")
    write(root / "sidecars" / "manager" / "sidecar.json", "{wired}")
    assert pf.changes(root, recorded) == pf.Changes([], [], [])
    assert all(pf.is_managed(p) for p in ("hooks.json", "mcp_config.json", "sidecars/manager/sidecar.json"))
    assert not pf.is_managed("skills/tdd/SKILL.md")


def test_changes_report_modified_removed_and_added(tmp_path):
    root = make_bundle(tmp_path / "p", **BASE)
    recorded = pf.pristine(root)
    write(root / "skills" / "tdd" / "SKILL.md", "tdd - mine\n")
    (root / "skills" / "docs" / "SKILL.md").unlink()
    write(root / "skills" / "mine" / "SKILL.md", "brand new\n")
    ch = pf.changes(root, recorded)
    assert ch == pf.Changes(modified=["skills/tdd/SKILL.md"], removed=["skills/docs/SKILL.md"],
                            added=["skills/mine/SKILL.md"])


def test_record_and_load_round_trip(tmp_path):
    root = make_bundle(tmp_path / "p", **BASE)
    f = tmp_path / "state" / "install.files.json"
    pf.record(root, f, "0.3.0")
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data["version"] == "0.3.0" and "skills/tdd/SKILL.md" in data["files"] and "hooks.json" not in data["files"]
    assert pf.load(f) == data["files"]
    assert pf.load(tmp_path / "nope.json") is None
    write(tmp_path / "bad.json", "{broken")
    assert pf.load(tmp_path / "bad.json") is None, "a damaged record is 'unknown', never an exception"


# --- reapplying the user's edits on top of a new version ------------------------------------------------------------


def update_scenario(tmp_path):
    old = make_bundle(tmp_path / "old", **BASE)
    recorded = pf.pristine(old)
    snapshot = tmp_path / "snapshot"
    import shutil
    shutil.copytree(old, snapshot)
    # the user's work, in the live (soon replaced) copy
    write(snapshot / "skills" / "tdd" / "SKILL.md", "tdd - mine\n")             # edited; upstream will not change it
    write(snapshot / "skills" / "docs" / "SKILL.md", "docs - mine\n")           # edited; upstream changes it too
    write(snapshot / "skills" / "mine" / "SKILL.md", "my own skill\n")          # added by the user
    (snapshot / "graphs" / "bugfix.yaml").unlink()                              # deleted by the user
    # the new version as installed
    new = make_bundle(tmp_path / "new", **{**BASE, "skills__docs__SKILL.md": "docs v2\n",
                                           "graphs__bugfix.yaml": "id: bugfix\nretries: 2\n",
                                           "skills__newskill__SKILL.md": "shiny\n"})
    return snapshot, new, recorded


def test_an_update_keeps_what_you_changed_and_tells_you_about_every_decision(tmp_path):
    snapshot, new, recorded = update_scenario(tmp_path)
    incoming = tmp_path / "incoming"
    rep = pf.reapply_user_edits(snapshot, new, recorded, pf.pristine(new), incoming)
    assert (new / "skills" / "tdd" / "SKILL.md").read_text() == "tdd - mine\n"      # upstream unchanged -> yours stays
    assert rep.kept == ["skills/tdd/SKILL.md"]
    assert (new / "skills" / "docs" / "SKILL.md").read_text() == "docs - mine\n"    # both changed -> yours wins ...
    assert (incoming / "skills" / "docs" / "SKILL.md").read_text() == "docs v2\n"   # ... and theirs is kept for you
    assert rep.conflicts == ["skills/docs/SKILL.md"]
    assert (new / "skills" / "mine" / "SKILL.md").read_text() == "my own skill\n"   # your own files survive
    assert rep.restored_added == ["skills/mine/SKILL.md"]
    assert not (new / "graphs" / "bugfix.yaml").exists()                            # you deleted it; it stays deleted
    assert (incoming / "graphs" / "bugfix.yaml").read_text() == "id: bugfix\nretries: 2\n"
    assert rep.kept_deleted == ["graphs/bugfix.yaml"]
    assert (new / "skills" / "newskill" / "SKILL.md").read_text() == "shiny\n"      # new upstream content arrives
    assert not rep.clean and rep.summary()


def test_a_file_upstream_no_longer_ships_is_not_resurrected(tmp_path):
    snapshot, new, recorded = update_scenario(tmp_path)
    (new / "skills" / "tdd" / "SKILL.md").unlink()
    (new / "skills" / "tdd").rmdir()
    new_pristine = pf.pristine(new)
    incoming = tmp_path / "incoming"
    rep = pf.reapply_user_edits(snapshot, new, recorded, new_pristine, incoming)
    assert not (new / "skills" / "tdd").exists()
    assert (incoming / "removed-upstream" / "skills" / "tdd" / "SKILL.md").read_text() == "tdd - mine\n"
    assert rep.orphaned == ["skills/tdd/SKILL.md"]


def test_nothing_edited_means_a_clean_report_and_no_files_moved(tmp_path):
    old = make_bundle(tmp_path / "old", **BASE)
    recorded = pf.pristine(old)
    new = make_bundle(tmp_path / "new", **{**BASE, "skills__tdd__SKILL.md": "tdd v2\n"})
    rep = pf.reapply_user_edits(old, new, recorded, pf.pristine(new), tmp_path / "incoming")
    assert rep.clean and (new / "skills" / "tdd" / "SKILL.md").read_text() == "tdd v2\n"
    assert not (tmp_path / "incoming").exists()


def test_without_a_record_the_running_bundle_stands_in_for_what_was_installed(tmp_path):
    old = make_bundle(tmp_path / "old", **BASE)
    bundle_of_installed_version = tmp_path / "bundle"
    make_bundle(bundle_of_installed_version, **BASE)
    write(old / "skills" / "tdd" / "SKILL.md", "tdd - mine\n")
    inferred = pf.pristine(bundle_of_installed_version)  # what `update` uses for installs made before records existed
    assert pf.changes(old, inferred).modified == ["skills/tdd/SKILL.md"]


# --- install() writes the record ------------------------------------------------------------------------------------


def test_install_records_what_it_installed_so_later_edits_are_detectable(home):
    install("global", with_gemini_md=False)
    from agflow import paths

    record_file = pf.record_path(paths.global_state_dir() / "install.json")
    recorded = pf.load(record_file)
    assert recorded and "skills/tdd/SKILL.md" in recorded
    plugin = paths.global_layout().plugins_dir / "agflow"
    assert pf.changes(plugin, recorded) == pf.Changes([], [], [])
    write(plugin / "skills" / "tdd" / "SKILL.md", "---\nname: tdd\n---\nmine\n")
    assert pf.changes(plugin, recorded).modified == ["skills/tdd/SKILL.md"]
