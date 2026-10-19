"""The Antigravity probe: reads versions and the shipped docs' vocabulary, offline, and never raises.

The docs used here are small synthetic ones in the same structure as the real ones."""

from __future__ import annotations

import json
import sys
from importlib import metadata
from pathlib import Path

import pytest

from agflow import antigravity_probe as ap

HOOKS_DOC = """# Hooks

## Hook Spec Fields

*   **`enabled`** (bool, optional): on or off.
*   **`PreToolUse`** (array, optional): before a tool.

## Supported Event Types

| Event            | When it fires | Structure |
| :--------------- | :------------ | :-------- |
| `PreToolUse`     | before a tool | Grouped   |
| `Stop`           | at the end    | Flat      |

### 1. `PreToolUse` Contract

*   **`decision`** (string, required):
    *   `"allow"`: let it run.
    *   `"deny"`: block it.

```json
{
  "toolCall": {"name": "run_command", // a comment
    "args": {"CommandLine": "x"}},
  "stepIdx": 19,
  ... (common fields)
}
```

```bash
npm test
```

```json
{ this is not json }
```
"""


def write_docs(root: Path, hooks: str = HOOKS_DOC) -> Path:
    docs = root / "antigravity" / "builtin" / "skills" / "agy-customizations" / "docs"
    docs.mkdir(parents=True)
    (docs / "hooks.md").write_text(hooks, encoding="utf-8")
    (docs.parent.parent / "plugin").mkdir()
    (docs.parent.parent / "plugin" / "SKILL.md").write_text("| `displayName` | string |\n", encoding="utf-8")
    return docs.parent.parent


# --- extraction -----------------------------------------------------------------------------------------------------


def test_markers_name_the_fields_events_values_and_json_keys():
    m = ap.markers_of(HOOKS_DOC)
    assert m["fields"] == ["PreToolUse", "decision", "enabled"]
    assert m["table_keys"] == ["PreToolUse", "Stop"]
    assert m["values"] == ["allow", "deny"]
    assert m["json_keys"] == ["CommandLine", "args", "name", "stepIdx", "toolCall"]
    # inline code only: quotes are stripped and fenced blocks are not scanned
    assert m["code_terms"] == ["PreToolUse", "Stop", "allow", "decision", "deny", "enabled"]
    assert m["layouts"] == ["PreToolUse=grouped", "Stop=flat"]
    assert m["limits"] == []
    assert m["tree_files"] == []


def test_layouts_follow_wrapped_table_rows_and_the_first_word_wins():
    text = (
        "| Event | When | Structure |\n"
        "| `PreToolUse` | before | Grouped (uses |\n"
        ":              : x      : matcher).     :\n"
        "| `Stop` | end | Flat (a list, not grouped). |\n"
        "| `Odd` | n/a | neither |\n"
        "plain line\n"
        ": a stray continuation\n"
    )
    assert ap.markers_of(text)["layouts"] == ["PreToolUse=grouped", "Stop=flat"]


def test_json_examples_that_do_not_parse_still_yield_their_keys():
    # a comment containing quotes, and a fence that runs on into prose - both seen in real docs
    text = (
        "```json\n{\n  \"terminationReason\": \"model_stop\", // e.g., \"model_stop\", \"error\"\n"
        "  \"fullyIdle\": true\n}\n```\n"
        "```json\n{\n  \"workspacePaths\": [\"/w\"],\n  \"modelName\": \"auto\"\n}\n\n> [!NOTE] prose, not json\n```\n"
    )
    assert ap.markers_of(text)["json_keys"] == ["fullyIdle", "modelName", "terminationReason", "workspacePaths"]


def test_slashes_inside_strings_are_not_comments():
    text = '```json\n{"url": "https://example.com/x", "name": "a"} // trailing\n```\n'
    assert ap.markers_of(text)["json_keys"] == ["name", "url"]


def test_directory_trees_name_their_files_and_folders():
    text = (
        "```text\n"
        "plugins/<plugin_name>/\n"
        "\u251c\u2500\u2500 plugin.json       # Required: Manifest file\n"
        "\u251c\u2500\u2500 hooks.json        # Optional\n"
        "\u2514\u2500\u2500 skills/           # Optional\n"
        "```\n"
    )
    assert ap.markers_of(text)["tree_files"] == ["hooks.json", "plugin.json", "skills/"]


def test_numeric_limits_in_inline_code_are_markers():
    text = "Each file is capped at `24 KB` / `24,000` bytes; the budget is `20,000` tokens; run `npm test`; `v2`."
    assert ap.markers_of(text)["limits"] == ["20,000", "24 KB", "24,000"]


def test_markers_are_total_on_junk_and_empty_input():
    for text in ("", "\x00\x01 not markdown", "```json\n{\n```", "| `unterminated"):
        assert set(ap.markers_of(text)) == set(ap.MARKER_KINDS)
    assert all(v == [] for v in ap.markers_of("").values())


def test_markers_are_capped():
    big = "\n".join(f"*   **`field{i}`** x" for i in range(1000))
    assert len(ap.markers_of(big)["fields"]) == ap.MAX_MARKERS
    many = " ".join(f"`word{i}`" for i in range(2000))
    assert len(ap.markers_of(many)["code_terms"]) == ap.MAX_CODE_TERMS


def test_fingerprint_ignores_line_endings():
    assert ap.fingerprint_text("a\r\nb\r\n") == ap.fingerprint_text("a\nb\n") != ap.fingerprint_text("a\nc\n")


# --- finding and reading the install --------------------------------------------------------------------------------


def test_the_skills_folder_with_the_hooks_doc_wins_over_one_that_merely_exists(tmp_path):
    (tmp_path / "antigravity" / "builtin" / "skills").mkdir(parents=True)  # exists, but has no docs
    cli = tmp_path / "antigravity-cli" / "builtin" / "skills" / "agy-customizations" / "docs"
    cli.mkdir(parents=True)
    (cli / "hooks.md").write_text("x", encoding="utf-8")
    assert ap.find_skills_root(tmp_path) == tmp_path / "antigravity-cli" / "builtin" / "skills"
    assert ap.find_skills_root(tmp_path / "nowhere") is None


def test_with_only_an_empty_skills_folder_that_folder_is_used(tmp_path):
    root = tmp_path / "antigravity-ide" / "builtin" / "skills"
    root.mkdir(parents=True)
    assert ap.find_skills_root(tmp_path) == root


def test_read_docs_hashes_and_marks_each_tracked_doc_that_exists(tmp_path):
    root = write_docs(tmp_path)
    hashes, markers, notes = ap.read_docs(root)
    assert set(hashes) == {"agy-customizations/docs/hooks.md", "plugin/SKILL.md"} and notes == []
    assert markers["plugin/SKILL.md"]["table_keys"] == ["displayName"]


def test_an_oversized_doc_is_skipped_with_a_note(tmp_path):
    root = write_docs(tmp_path)
    (root / "agy-customizations" / "docs" / "rules.md").write_text("x" * (ap.MAX_DOC_BYTES + 1), encoding="utf-8")
    hashes, _, notes = ap.read_docs(root)
    assert "agy-customizations/docs/rules.md" not in hashes and any("rules.md" in n for n in notes)


def test_a_doc_with_bad_bytes_is_still_read(tmp_path):
    root = write_docs(tmp_path)
    (root / "agy-customizations" / "docs" / "hooks.md").write_bytes(b"| `Stop` |\n\xff\xfe broken")
    hashes, markers, _ = ap.read_docs(root)
    rel = "agy-customizations/docs/hooks.md"
    assert hashes[rel] and markers[rel]["table_keys"] == ["Stop"]


# --- running --version ----------------------------------------------------------------------------------------------


def py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


@pytest.mark.parametrize("code, expected", [
    ("print('1.3.3')", "1.3.3"),
    ("print('gemini cli 0.9.1-beta.2 (build 7)')", "0.9.1-beta.2"),
    ("import sys; print('agy 2.0', file=sys.stderr)", "2.0"),
    ("print('no digits here')", None),
    ("pass", None),
])
def test_tool_version_reads_the_first_version_it_prints(code, expected):
    assert ap.tool_version(py(code)) == expected


def test_tool_version_survives_a_missing_a_slow_and_a_broken_tool(tmp_path):
    assert ap.tool_version([str(tmp_path / "no-such-tool")]) is None
    assert ap.tool_version(py("import threading; threading.Event().wait(30)"), timeout=0.3) is None
    assert ap.tool_version(py("raise SystemExit(3)")) is None


def test_find_agy_prefers_path_then_the_local_install(tmp_path, monkeypatch):
    monkeypatch.setattr(ap.shutil, "which", lambda name: "/usr/bin/agy")
    assert ap.find_agy() == ["/usr/bin/agy"]
    monkeypatch.setattr(ap.shutil, "which", lambda name: None)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(ap.paths, "home", lambda: tmp_path)
    assert ap.find_agy() is None
    (tmp_path / ".local" / "bin").mkdir(parents=True)
    (tmp_path / ".local" / "bin" / "agy").write_text("", encoding="utf-8")
    assert ap.find_agy() == [str(tmp_path / ".local" / "bin" / "agy")]


def test_sdk_version_is_none_when_missing_or_damaged(monkeypatch):
    def missing(name):
        raise metadata.PackageNotFoundError(name)

    def damaged(name):
        raise RuntimeError("broken metadata")

    monkeypatch.setattr(ap.metadata, "version", missing)
    assert ap.sdk_version() is None
    monkeypatch.setattr(ap.metadata, "version", damaged)
    assert ap.sdk_version() is None
    monkeypatch.setattr(ap.metadata, "version", lambda name: "0.1.20")
    assert ap.sdk_version() == "0.1.20"


# --- the whole probe ------------------------------------------------------------------------------------------------


def test_probe_of_an_empty_home_is_a_calm_answer(tmp_path):
    snap = ap.probe(tmp_path, agy=None, gemini_cli=None)
    assert not snap.has_docs and snap.agy_version is None and snap.skills_root is None
    assert any("no Antigravity skills folder" in n for n in snap.notes)


def test_probe_collects_versions_docs_skills_and_config_names(tmp_path):
    write_docs(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.json").write_text(json.dumps({"plugins": {"x": 1}, "userSettings": "s3cret-value"}),
                                                     encoding="utf-8")
    snap = ap.probe(tmp_path, agy=py("print('1.3.3')"), gemini_cli=py("print('0.9.0')"))
    assert snap.agy_version == "1.3.3" and snap.gemini_cli_version == "0.9.0"
    assert snap.has_docs and snap.builtin_skills == ["agy-customizations", "plugin"]
    assert snap.config_keys == ["plugins", "userSettings"]
    assert "s3cret-value" not in json.dumps(snap.to_json()), "only key names may be stored, never values"


def test_probe_notes_an_agy_that_prints_no_version(tmp_path):
    snap = ap.probe(tmp_path, agy=py("pass"), gemini_cli=None)
    assert snap.agy_version is None and any("--version" in n for n in snap.notes)


def test_snapshot_round_trips_through_json(tmp_path):
    write_docs(tmp_path)
    snap = ap.probe(tmp_path, agy=py("print('1.3.3')"), gemini_cli=None)
    again = ap.Snapshot.from_json(json.loads(json.dumps(snap.to_json())))
    assert again.agy_version == snap.agy_version and again.doc_hashes == snap.doc_hashes
    assert again.markers == snap.markers and again.builtin_skills == snap.builtin_skills


@pytest.mark.parametrize("junk", [None, 5, "x", [], {"doc_hashes": 3, "markers": [1], "builtin_skills": "no"},
                                  {"markers": {"a": {"fields": [1, "ok", None]}, 2: {}}, "agy_version": 3}])
def test_snapshot_from_junk_is_empty_not_an_error(junk):
    snap = ap.Snapshot.from_json(junk)
    assert snap.agy_version is None and snap.doc_hashes == {} and snap.builtin_skills == []
    assert snap.markers in ({}, {"a": {"fields": ["ok"]}})
