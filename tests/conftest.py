from __future__ import annotations

import os
from pathlib import Path

import pytest

from agflow import paths

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin"


@pytest.fixture(autouse=True, scope="session")
def _sandbox_guard(tmp_path_factory: pytest.TempPathFactory):
    """Fail loudly if any code resolves the user's home outside pytest's temp tree.

    The per-test ``home`` fixture isolates $HOME with ``monkeypatch``; a test that calls ``monkeypatch.undo()``
    would silently restore the real one and the next ``install()`` would rewrite the developer's actual
    ~/.gemini. This guard is installed once per session with its own MonkeyPatch, so a test cannot undo it.
    """
    base = tmp_path_factory.getbasetemp().resolve()
    real_home = paths.home

    def guarded_home() -> Path:
        h = real_home()
        try:
            h.resolve().relative_to(base)
        except ValueError:
            raise RuntimeError(f"test escaped its sandbox: HOME resolves to {h}, outside {base}. Never call "
                               "monkeypatch.undo() in a test; use `with monkeypatch.context() as mp:`") from None
        return h

    mp = pytest.MonkeyPatch()
    mp.setattr(paths, "home", guarded_home)
    yield
    mp.undo()


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


def symlinks_supported(probe_dir: Path) -> bool:
    """Whether this process may create symlinks (Windows needs Developer Mode or elevation)."""
    probe_dir.mkdir(parents=True, exist_ok=True)
    target, link = probe_dir / "probe-target", probe_dir / "probe-link"
    target.write_text("x", encoding="utf-8")
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        return False
    finally:
        link.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
    return True


@pytest.fixture
def require_symlinks(tmp_path: Path) -> None:
    """Skip (with the reason) on systems where symlink creation is not permitted."""
    if not symlinks_supported(tmp_path / "symlink-probe"):
        pytest.skip("creating symlinks is not permitted here (Windows without Developer Mode/elevation)")


@pytest.fixture
def graphs_dir(workspace: Path) -> Path:
    d = paths.workspace_layout(workspace).graphs_dir
    assert d is not None
    d.mkdir(parents=True)
    return d
