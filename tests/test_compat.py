"""The compatibility check: what is breaking, what drifted, what is merely reworded - and that the shipped baseline
really describes the hooks, manifests and vocabulary AG-Flow ships.

Snapshots are built from small synthetic docs; nothing here depends on the Antigravity installed on the machine."""

from __future__ import annotations

import importlib.util
import json
import os
import time
from pathlib import Path

import pytest

from agflow import antigravity_probe as ap
from agflow import compat, paths, platform_vocab

HOOKS = "agy-customizations/docs/hooks.md"
PLUGIN = "plugin/SKILL.md"

HOOKS_DOC = """## Supported Event Types

| Event | When | Structure |
| :--- | :--- | :--- |
| `PreToolUse` | before | Grouped |
| `Stop` | end | Flat |

*   **`decision`** (string): the answer.
    *   `"deny"`: block it.
"""
PLUGIN_DOC = "| `name` | string |\n| `version` | string |\n"


def snap(hooks: str = HOOKS_DOC, plugin: str | None = PLUGIN_DOC, *, agy: str | None = "1.3.3", **extra) -> ap.Snapshot:
    docs = {HOOKS: hooks}
    if plugin is not None:
        docs[PLUGIN] = plugin
    s = ap.Snapshot(agy_version=agy, agy_path="/usr/bin/agy" if agy else None, skills_root="/x",
                    doc_hashes={d: ap.fingerprint_text(t) for d, t in docs.items()},
                    markers={d: ap.markers_of(t) for d, t in docs.items()})
    for key, value in extra.items():
        setattr(s, key, value)
    return s


def req(doc: str, kind: str, value: str, component: str = "hooks") -> dict:
    return {"doc": doc, "kind": kind, "value": value, "component": component, "why": "test"}


REQUIRES = [req(HOOKS, "table_keys", "PreToolUse"), req(HOOKS, "values", "deny"), req(PLUGIN, "table_keys", "name",
                                                                                      "plugin manifest")]


def baseline(verified: ap.Snapshot, requires=REQUIRES, tested=()) -> dict:
    return {"schema": 1, "verified": verified.to_json(), "tested_antigravity": list(tested), "requires": list(requires)}


def codes(report: compat.Report, level: str | None = None) -> list[str]:
    return [f.code for f in report.findings if level is None or f.level == level]


# --- the verdicts ---------------------------------------------------------------------------------


def test_the_antigravity_it_was_verified_against_is_ok():
    report = compat.check(snap(), baseline(snap()))
    assert report.status == "ok" and report.findings == [] and report.exit_code == 0


def test_a_missing_requirement_is_breaking_and_names_the_component():
    changed = snap(HOOKS_DOC.replace("`PreToolUse`", "`BeforeTool`"))
    report = compat.check(changed, baseline(snap()))
    assert report.status == "breaking" and report.exit_code == 2
    [finding] = report.at_level("breaking")
    assert finding.code == "requirement-missing" and finding.component == "hooks"
    assert "`PreToolUse`" in finding.message and "table keys" in finding.message and "test" in finding.message
    assert "agflow update check" in finding.action
    assert "drift" in {f.level for f in report.findings}, "the rename is also reported as a change"


def test_a_requirement_carries_its_own_action_when_it_has_one():
    entry = dict(req(HOOKS, "values", "deny"), action="Do this specific thing.")
    report = compat.check(snap(HOOKS_DOC.replace('"deny"', '"block"')), baseline(snap(), [entry]))
    assert report.at_level("breaking")[0].action == "Do this specific thing."


def test_requirements_for_a_doc_that_is_not_there_are_not_called_breaking():
    report = compat.check(snap(plugin=None), baseline(snap()))
    assert report.status == "drift"  # the plugin doc is gone: a change to re-verify, not a broken dependency
    assert codes(report, "drift") == ["doc-gone"] and not report.at_level("breaking")


def test_no_installation_at_all_is_unknown_and_not_an_alarm():
    nothing = ap.Snapshot()
    report = compat.check(nothing, baseline(snap()))
    assert report.status == "unknown" and report.exit_code == 0
    assert set(codes(report)) == {"docs-missing", "agy-missing"} and not report.at_level("breaking")


def test_a_missing_or_damaged_baseline_is_unknown_not_a_crash():
    assert compat.check(snap(), {}).status == "unknown"
    assert "baseline-missing" in codes(compat.check(snap(), {}))
    bad = baseline(snap(), requires=[{"doc": "x"}, "junk", dict(req(HOOKS, "nonsense", "v"))])
    report = compat.check(snap(), bad)
    assert codes(report).count("baseline-malformed") == 3 and report.status == "ok"


# --- what changed since it was verified -----------------------------------------------------------


def test_a_reworded_doc_with_the_same_names_is_only_noted():
    reworded = snap(HOOKS_DOC + "\nSome extra explanatory sentence.\n")
    report = compat.check(reworded, baseline(snap()))
    assert report.status == "ok" and codes(report) == ["doc-reworded"] and report.findings[0].level == "info"


def test_new_and_removed_structural_names_are_drift_and_listed():
    grown = snap(HOOKS_DOC + "| `PreCompaction` | compacting | Flat |\n")
    report = compat.check(grown, baseline(snap()))
    assert report.status == "drift" and report.exit_code == 1
    [finding] = [f for f in report.findings if f.code == "markers-changed"]
    assert "new table keys: PreCompaction" in finding.message and "PreCompaction=flat" in finding.message
    assert "agflow sync apply" in finding.action
    shrunk = snap(HOOKS_DOC.replace("| `Stop` | end | Flat |\n", ""))
    assert "gone table keys: Stop" in compat.check(shrunk, baseline(snap())).findings[0].message


def test_a_changed_layout_is_drift_even_though_the_event_names_match():
    flipped = snap(HOOKS_DOC.replace("`Stop` | end | Flat", "`Stop` | end | Grouped"))
    report = compat.check(flipped, baseline(snap()))
    [finding] = [f for f in report.findings if f.code == "markers-changed"]
    assert "gone layouts: Stop=flat" in finding.message and "new layouts: Stop=grouped" in finding.message


def test_prose_only_changes_do_not_change_the_status():
    mentions = snap(HOOKS_DOC + "\nSee also `someNewWord`.\n")
    report = compat.check(mentions, baseline(snap()))
    assert report.status == "ok" and codes(report) == ["docs-mention-changed"]


def test_a_new_doc_is_drift():
    report = compat.check(snap(), baseline(snap(plugin=None)))
    assert report.status == "drift" and codes(report, "drift") == ["doc-new"]


def test_many_changed_names_are_summarised():
    many = "".join(f"| `Event{i}` | x | Flat |\n" for i in range(20))
    message = compat.check(snap(HOOKS_DOC + many), baseline(snap())).findings[0].message
    assert "(+" in message and "more)" in message and message.count("Event") <= compat.SHOWN * 2 + 4


# --- versions -------------------------------------------------------------------------------------


@pytest.mark.parametrize("now, tested, level", [
    ("1.3.3", (), None),            # unchanged
    ("1.3.4", (), "info"),          # patch
    ("1.4.0", (), "info"),          # minor
    ("1.4.0", ("1.4.0",), None),    # already tested
    ("2.0.0", (), "drift"),         # major
    ("2.0.0", ("2.0.0",), None),
    ("1.2.0", (), "info"),          # older
    ("not-a-version", (), None),    # unparsable: nothing to say
])
def test_antigravity_version_changes(now, tested, level):
    report = compat.check(snap(agy=now), baseline(snap(), tested=tested))
    levels = [f.level for f in report.findings if f.code.startswith("agy-")]
    assert levels == ([level] if level else [])


def test_sdk_and_gemini_cli_moves_are_noted_but_a_patch_is_not():
    verified = snap(sdk_version="0.1.20", gemini_cli_version="0.9.0")
    moved = snap(sdk_version="0.2.0", gemini_cli_version="0.9.5")
    report = compat.check(moved, baseline(verified))
    messages = [f.message for f in report.findings if f.code == "tool-version"]
    assert messages == ["Antigravity SDK moved from 0.1.20 to 0.2.0."] and report.status == "ok"


# --- reports --------------------------------------------------------------------------------------


def test_report_round_trips_and_junk_becomes_an_empty_unknown_report():
    report = compat.check(snap(HOOKS_DOC + "| `X` | y | Flat |\n"), baseline(snap()), now="2026-10-16T00:00:00Z")
    again = compat.Report.from_json(json.loads(compat.dumps(report)))
    assert again.to_json() == report.to_json() and again.checked_at == "2026-10-16T00:00:00Z"
    for junk in (None, 3, "x", [], {"status": "weird", "findings": "no"}, {"findings": [1, {"level": "nope"}]}):
        got = compat.Report.from_json(junk)
        assert got.status == "unknown" and got.findings == []


def test_lines_put_the_worst_first_and_show_the_action():
    report = compat.check(snap(HOOKS_DOC.replace("`PreToolUse`", "`X`")), baseline(snap()))
    text = "\n".join(report.lines())
    assert text.index("[breaking]") < text.index("[drift]") and "-> " in text
    assert text.startswith("Antigravity no longer documents")
    assert compat.Report().lines()[0].startswith("AG-Flow could not verify")


# --- the shipped baseline -------------------------------------------------------------------------


def test_the_shipped_baseline_is_well_formed_and_holds_against_itself():
    base = compat.shipped()
    assert base["schema"] == compat.SCHEMA
    requires, problems = compat.parse_requirements(base)
    assert problems == [] and len(requires) == len(base["requires"]) >= 30
    verified = ap.Snapshot.from_json(base["verified"])
    assert verified.has_docs and verified.agy_version and verified.agy_version in base["tested_antigravity"]
    report = compat.check(verified, base)
    assert report.status == "ok" and report.findings == [], report.lines()


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def test_the_shipped_baseline_carries_no_path_of_the_machine_it_was_made_on():
    """It is published inside the wheel: a home folder in it would name the person who generated it."""
    base = json.loads(compat.BASELINE_FILE.read_text(encoding="utf-8"))
    home = str(Path.home())
    for text in _strings(base):
        assert home not in text and Path.home().name not in text.replace("\\", "/").split("/"), text
        assert not (len(text) > 2 and text[1] == ":" and text[2] in "\\/"), f"a drive path: {text}"


def test_the_baseline_generator_stores_paths_relative_to_the_home_folder():
    """``scripts/make_compat_baseline.py`` must not write the home folder of whoever runs it into the shipped file."""
    script = Path(__file__).resolve().parent.parent / "scripts" / "make_compat_baseline.py"
    spec = importlib.util.spec_from_file_location("make_compat_baseline", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    home = Path.home()
    assert module.portable(str(home / ".gemini" / "antigravity" / "skills")) == "~/.gemini/antigravity/skills"
    assert module.portable(str(home.parent / "someone-else" / "skills")) is None
    assert module.portable("") is None and module.portable(None) is None


def test_the_baseline_requires_every_hook_event_the_plugin_ships_with_the_right_layout():
    hooks = json.loads((paths.bundled_plugin_dir() / "hooks.json").read_text(encoding="utf-8"))
    requires, _ = compat.parse_requirements(compat.shipped())
    wanted = {(r.kind, r.value) for r in requires if r.doc == HOOKS}
    for events in hooks.values():
        for event, handlers in events.items():
            grouped = isinstance(handlers[0], dict) and "hooks" in handlers[0]
            assert ("table_keys", event) in wanted, f"{event} is shipped but not a requirement"
            assert ("layouts", f"{event}={'grouped' if grouped else 'flat'}") in wanted


def test_the_baseline_agrees_with_the_platform_vocabulary_on_hook_events_and_layouts():
    vocab = platform_vocab.builtin()
    markers = ap.Snapshot.from_json(compat.shipped()["verified"]).markers[HOOKS]
    assert sorted(vocab.hook_events) == markers["table_keys"]
    expected = sorted(f"{e}={'grouped' if e in vocab.hook_events_grouped else 'flat'}" for e in vocab.hook_events)
    assert markers["layouts"] == expected


def test_every_decision_the_hooks_emit_is_a_requirement():
    requires, _ = compat.parse_requirements(compat.shipped())
    have = {r.value for r in requires if r.doc == HOOKS}
    assert {"deny", "ask", "continue", "decision", "reason"} <= have


# --- accepting a newer Antigravity ----------------------------------------------------------------


def changed_snapshot() -> ap.Snapshot:
    """The shipped verified snapshot plus one new hook event: what a Google update could look like."""
    new = ap.Snapshot.from_json(compat.shipped()["verified"])
    new.markers[HOOKS]["table_keys"] = sorted([*new.markers[HOOKS]["table_keys"], "PreCompaction"])
    new.doc_hashes[HOOKS] = "0" * 64
    new.agy_version = "1.3.4"
    return new


def test_acknowledging_a_change_stops_the_alarm_and_forgetting_brings_it_back(tmp_path):
    changed = changed_snapshot()
    assert compat.run(tmp_path, snapshot=changed, save=False).status == "drift"
    where = compat.accept(changed, tmp_path)
    assert where == compat.accepted_path(tmp_path) and where.is_file()
    after = compat.run(tmp_path, snapshot=changed, save=False)
    assert after.status == "ok" and after.baseline == "accepted" and after.findings == []
    assert compat.forget_accepted(tmp_path) is True and compat.forget_accepted(tmp_path) is False
    assert compat.run(tmp_path, snapshot=changed, save=False).status == "drift"


def test_an_accepted_baseline_keeps_the_shipped_requirements(tmp_path):
    changed = changed_snapshot()
    changed.markers[HOOKS]["table_keys"].remove("Stop")  # a dependency disappears in the newer version
    compat.accept(changed, tmp_path)
    report = compat.run(tmp_path, snapshot=changed, save=False)
    assert report.status == "breaking", "accepting a change must never excuse a missing requirement"


def test_an_accepted_baseline_stores_names_and_hashes_only(tmp_path):
    compat.accept(changed_snapshot(), tmp_path)
    stored = json.loads(compat.accepted_path(tmp_path).read_text(encoding="utf-8"))
    assert set(stored) == {"schema", "accepted_at", "agflow", "snapshot", "tested_antigravity"}
    assert stored["tested_antigravity"] == ["1.3.4"]


@pytest.mark.parametrize("content", ["", "{broken", "[]", '{"schema": 99, "snapshot": {}}', '{"schema": 1}'])
def test_a_damaged_accepted_baseline_falls_back_to_the_shipped_one(tmp_path, content):
    compat.accepted_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    compat.accepted_path(tmp_path).write_text(content, encoding="utf-8")
    base, source = compat.effective(tmp_path)
    assert source == "shipped" and base == compat.shipped()


def test_with_no_shipped_baseline_there_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(compat, "BASELINE_FILE", tmp_path / "missing.json")
    assert compat.effective(tmp_path) == ({}, "none")
    assert compat.run(tmp_path, snapshot=snap(), save=False).status == "unknown"
    (tmp_path / "bad.json").write_text("{nope", encoding="utf-8")
    monkeypatch.setattr(compat, "BASELINE_FILE", tmp_path / "bad.json")
    assert compat.shipped() == {}


# --- remembered results ---------------------------------------------------------------------------


def test_run_remembers_the_last_result_unless_told_not_to(tmp_path):
    verified = ap.Snapshot.from_json(compat.shipped()["verified"])
    compat.run(tmp_path, snapshot=verified, save=False)
    assert compat.load_last(tmp_path) is None
    report = compat.run(tmp_path, snapshot=verified)
    assert compat.load_last(tmp_path).to_json() == report.to_json()


def test_a_stale_or_damaged_last_result_is_ignored(tmp_path):
    report = compat.run(tmp_path, snapshot=ap.Snapshot.from_json(compat.shipped()["verified"]))
    assert compat.load_last(tmp_path, max_age_seconds=3600) is not None
    old = time.time() - 7200
    os.utime(compat.last_path(tmp_path), (old, old))
    assert compat.load_last(tmp_path, max_age_seconds=3600) is None
    assert compat.load_last(tmp_path).status == report.status  # no age limit: still readable
    compat.last_path(tmp_path).write_text("{broken", encoding="utf-8")
    assert compat.load_last(tmp_path) is None


def test_saving_the_result_never_raises_when_the_folder_is_unwritable(tmp_path):
    blocker = tmp_path / "state"
    blocker.write_text("a file where a folder should be", encoding="utf-8")
    compat.save_last(compat.Report(), blocker)  # must not raise
    assert isinstance(blocker, Path)


@pytest.mark.parametrize("status, code", [("ok", 0), ("unknown", 0), ("drift", 1), ("breaking", 2), ("weird", 0)])
def test_exit_codes(status, code):
    assert compat.Report(status=status).exit_code == code
