"""scripts/make_release.py: the artifacts the updater verifies are built consistently and checked like a client would.

The real ``uv build`` is replaced by a stub that writes a wheel from tests/fake_update_env.py; scripts/smoke_release.py
runs the genuine build and update."""

from __future__ import annotations

import importlib.util
import json
import sys
import tarfile
from pathlib import Path

import pytest

from agflow import release_feed as rf
from tests.conftest import REPO
from tests.fake_update_env import FakeEnv


def load_script():
    spec = importlib.util.spec_from_file_location("make_release", REPO / "scripts" / "make_release.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["make_release"] = module
    spec.loader.exec_module(module)
    return module


mr = load_script()


def tree_with_versions(root: Path, init="0.4.0", pyproject="0.4.0", plugin="0.4.0", requires=">=3.10") -> Path:
    (root / "src" / "agflow").mkdir(parents=True)
    (root / "plugin").mkdir()
    (root / "src" / "agflow" / "__init__.py").write_text(f'"""x"""\n__version__ = "{init}"\n', encoding="utf-8")
    (root / "pyproject.toml").write_text(f'[project]\nname = "agflow"\nversion = "{pyproject}"\n'
                                         f'requires-python = "{requires}"\n', encoding="utf-8")
    (root / "plugin" / "plugin.json").write_text(json.dumps({"name": "agflow", "version": plugin}), encoding="utf-8")
    return root


@pytest.fixture
def fake_env(tmp_path, plugin_dir):
    return FakeEnv(tmp_path / "fake", plugin_dir)


def stub_builder(fake_env, version="0.4.0", wheels=1, sdists=1, code=0):
    def run(argv):
        out = Path(argv[argv.index("--out-dir") + 1])
        out.mkdir(parents=True, exist_ok=True)
        for i in range(wheels):
            (out / f"agflow-{version}{'' if i == 0 else f'.{i}'}-py3-none-any.whl").write_bytes(
                fake_env.make_wheel(version))
        for i in range(sdists):
            path = out / f"agflow-{version}{'' if i == 0 else f'.{i}'}.tar.gz"
            with tarfile.open(path, "w:gz"):
                pass
        return type("Done", (), {"returncode": code, "stdout": "out", "stderr": "err"})()
    return run


# --- versions -------------------------------------------------------------------------------------


def test_the_repository_versions_agree():
    found = mr.read_versions(REPO)
    assert set(found) == {"agflow/__init__.py", "pyproject.toml", "plugin/plugin.json"}
    assert len(set(found.values())) == 1


def test_a_version_that_differs_between_files_is_named(tmp_path):
    root = tree_with_versions(tmp_path, pyproject="0.4.1")
    with pytest.raises(mr.ReleaseError, match=r"pyproject.toml = 0\.4\.1.*plugin/plugin\.json = 0\.4\.0"):
        mr.read_versions(root)


@pytest.mark.parametrize("damage", ["init", "pyproject", "plugin-json", "plugin-missing"])
def test_missing_or_unreadable_version_sources_are_a_release_error(tmp_path, damage):
    root = tree_with_versions(tmp_path)
    {"init": lambda: (root / "src" / "agflow" / "__init__.py").write_text("no version", encoding="utf-8"),
     "pyproject": lambda: (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8"),
     "plugin-json": lambda: (root / "plugin" / "plugin.json").write_text("{broken", encoding="utf-8"),
     "plugin-missing": lambda: (root / "plugin" / "plugin.json").unlink()}[damage]()
    with pytest.raises(mr.ReleaseError, match="differs between files"):
        mr.read_versions(root)


# --- the files ------------------------------------------------------------------------------------


def test_sums_use_the_sha256sum_format_and_round_trip(tmp_path):
    (tmp_path / "b.whl").write_bytes(b"bbb")
    (tmp_path / "a.tar.gz").write_bytes(b"aaa")
    text = mr.sums_text([tmp_path / "b.whl", tmp_path / "a.tar.gz"])
    lines = text.splitlines()
    assert text.endswith("\n") and "\r" not in text and [ln.split("  ")[1] for ln in lines] == ["a.tar.gz", "b.whl"]
    parsed = rf.parse_sums(text)
    assert parsed == {"a.tar.gz": mr.sha256_file(tmp_path / "a.tar.gz"), "b.whl": mr.sha256_file(tmp_path / "b.whl")}


def test_the_release_json_carries_only_what_was_given():
    plain = mr.release_meta("0.4.0", python_spec=">=3.10", tested=["1.3.3"])
    assert plain == {"version": "0.4.0", "tested_antigravity": ["1.3.3"], "python": ">=3.10"}
    full = mr.release_meta("0.4.0", python_spec=">=3.10", tested=[], min_antigravity="1.3.0", breaking=True,
                           notes="n" * 5000)
    assert full["min_antigravity"] == "1.3.0" and full["breaking"] is True and len(full["notes"]) == 2000
    parsed = rf.parse_meta(json.dumps(full))
    assert parsed.min_antigravity == (1, 3, 0) and parsed.breaking and parsed.python == ">=3.10"


def test_the_python_requirement_and_tested_antigravity_come_from_the_repository():
    assert mr.requires_python(REPO) == ">=3.10"
    assert "1.3.3" in mr.tested_antigravity(REPO)
    assert mr.tested_antigravity(REPO / "nowhere") == []


# --- building -------------------------------------------------------------------------------------


def test_a_release_is_built_signed_off_and_checked(tmp_path, fake_env):
    root = tree_with_versions(tmp_path / "repo")
    out = mr.make(root, tmp_path / "dist" / "release", min_antigravity="1.3.0", run=stub_builder(fake_env))
    names = sorted(p.name for p in out.iterdir())
    assert names == ["SHA256SUMS", "agflow-0.4.0-py3-none-any.whl", "agflow-0.4.0.tar.gz", "release.json"]
    meta = rf.parse_meta((out / "release.json").read_text(encoding="utf-8"))
    assert meta.version == "0.4.0" and meta.min_antigravity == (1, 3, 0) and meta.python == ">=3.10"
    sums = rf.parse_sums((out / "SHA256SUMS").read_text(encoding="utf-8"))
    assert set(sums) == {"agflow-0.4.0-py3-none-any.whl", "agflow-0.4.0.tar.gz"}


def test_files_uv_leaves_in_the_output_folder_are_not_release_files(tmp_path, fake_env):
    root = tree_with_versions(tmp_path / "repo")
    plain = stub_builder(fake_env)

    def with_gitignore(argv):
        done = plain(argv)
        (Path(argv[argv.index("--out-dir") + 1]) / ".gitignore").write_text("*\n", encoding="utf-8")
        return done

    out = mr.make(root, tmp_path / "dist" / "release", run=with_gitignore)
    assert ".gitignore" in {p.name for p in out.iterdir()}
    assert ".gitignore" not in rf.parse_sums((out / "SHA256SUMS").read_text(encoding="utf-8"))


def test_a_stale_output_folder_is_replaced_not_mixed_in(tmp_path, fake_env):
    root = tree_with_versions(tmp_path / "repo")
    out = tmp_path / "dist" / "release"
    out.mkdir(parents=True)
    (out / "agflow-0.0.1-py3-none-any.whl").write_bytes(b"left over from an earlier build")
    mr.make(root, out, run=stub_builder(fake_env))
    assert not (out / "agflow-0.0.1-py3-none-any.whl").exists()


@pytest.mark.parametrize("builder, message", [
    (dict(code=1), "uv build failed"),
    (dict(wheels=0), "expected one wheel and one sdist"),
    (dict(wheels=2), "expected one wheel and one sdist"),
    (dict(sdists=0), "expected one wheel and one sdist"),
])
def test_a_failed_or_odd_build_is_a_release_error(tmp_path, fake_env, builder, message):
    root = tree_with_versions(tmp_path / "repo")
    with pytest.raises(mr.ReleaseError, match=message):
        mr.make(root, tmp_path / "dist" / "release", run=stub_builder(fake_env, **builder))


def test_a_wheel_the_updater_would_refuse_fails_the_release(tmp_path, fake_env):
    root = tree_with_versions(tmp_path / "repo", init="0.4.0", pyproject="0.4.0", plugin="0.4.0")
    with pytest.raises(mr.ReleaseError, match="would be refused by the updater"):
        mr.make(root, tmp_path / "dist" / "release", run=stub_builder(fake_env, version="0.4.1"))


def test_verification_catches_a_changed_file_and_a_wrong_release_json(tmp_path, fake_env):
    root = tree_with_versions(tmp_path / "repo")
    out = mr.make(root, tmp_path / "dist" / "release", run=stub_builder(fake_env))
    (out / "agflow-0.4.0.tar.gz").write_bytes(b"swapped after the sums were written")
    with pytest.raises(mr.ReleaseError, match="SHA256SUMS does not match agflow-0.4.0.tar.gz"):
        mr.verify(out, "0.4.0")
    (out / "SHA256SUMS").write_text(mr.sums_text(list(out.glob("agflow-*"))), encoding="utf-8")
    mr.verify(out, "0.4.0")
    (out / "release.json").write_text(json.dumps({"version": "9.9.9"}), encoding="utf-8")
    with pytest.raises(mr.ReleaseError, match="different version"):
        mr.verify(out, "0.4.0")
    (out / "agflow-0.4.0-py3-none-any.whl").unlink()
    with pytest.raises(mr.ReleaseError, match="no wheel"):
        mr.verify(out, "0.4.0")


def test_the_output_folder_must_be_inside_dist(tmp_path, capsys):
    assert mr.main(["--out", str(tmp_path)]) == 2
    assert "must be inside" in capsys.readouterr().err
    assert mr.main(["--out", str(REPO)]) == 2, "never empties the repository itself"
