from __future__ import annotations

from pathlib import Path

import pytest

from agflow import paths

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin"


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test gets an isolated $HOME so nothing touches the real ~/.gemini."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    monkeypatch.delenv("AGFLOW_WORKSPACE", raising=False)
    monkeypatch.delenv("AGFLOW_GUARD", raising=False)
    return h


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / ".git").mkdir(parents=True)
    return ws


@pytest.fixture
def plugin_dir() -> Path:
    return PLUGIN


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def graphs_dir(workspace: Path) -> Path:
    d = paths.workspace_layout(workspace).graphs_dir
    assert d is not None
    d.mkdir(parents=True)
    return d
