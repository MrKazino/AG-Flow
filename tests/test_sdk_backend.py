"""SdkBackend builds a real google-antigravity LocalAgentConfig (no model call is made)."""

import pytest

pytest.importorskip("google.antigravity")

from google.antigravity import BuiltinTools  # noqa: E402

from agflow.graph import engine  # noqa: E402
from agflow.graph.catalog import resolve  # noqa: E402
from agflow.graph.schema import coerce_inputs  # noqa: E402
from agflow.install import install  # noqa: E402
from agflow.runner.backends import NodeResult, SdkBackend, guard_predicate, model_for_tier  # noqa: E402


def card_for(workspace, graph_id, node_id, inputs):
    g, _ = resolve(graph_id, workspace)
    s = engine.new_run(g, coerce_inputs(g, inputs), str(workspace), now=0)
    for _ in range(10):
        claimed = engine.claim(g, s, now=1)
        if node_id in claimed:
            return engine.build_card(g, s, node_id)
        for nid in claimed:
            engine.report(g, s, nid, "succeeded", now=1)
    raise AssertionError(node_id)


def test_build_config_from_agent_file(workspace, monkeypatch):
    install("global", with_gemini_md=False)
    monkeypatch.setenv("AGFLOW_MODEL_PRO", "gemini-pro-test")
    backend = SdkBackend(workspace)
    card = card_for(workspace, "bugfix", "review", {"bug": "x"})
    cfg = backend.build_config(card)
    assert cfg.system_instructions.startswith("# Reviewer")
    enabled = set(cfg.capabilities.enabled_tools)
    assert BuiltinTools.RUN_COMMAND in enabled and BuiltinTools.FINISH in enabled
    assert BuiltinTools.EDIT_FILE not in enabled  # reviewer is read-only
    assert cfg.response_schema is not None
    assert cfg.workspaces == [str(workspace.resolve())]
    assert any(p.endswith("skills") for p in cfg.skills_paths)
    assert {p.name for p in cfg.policies} >= {"agflow-guard"}


def test_exec_off_denies_commands(workspace):
    install("global", with_gemini_md=False)
    card = card_for(workspace, "security-sweep", "report", {})  # researcher: commandExecutionPolicy off
    cfg = SdkBackend(workspace).build_config(card)
    assert "agflow-exec-off" in {p.name for p in cfg.policies}


def test_guard_predicate():
    headless = guard_predicate(allow_risky=False)
    risky_ok = guard_predicate(allow_risky=True)
    assert headless({"CommandLine": "rm -rf /"}) and risky_ok({"CommandLine": "rm -rf /"})
    assert headless({"CommandLine": "git push --force"}) and not risky_ok({"CommandLine": "git push --force"})
    assert not headless({"CommandLine": "pytest -q"})


def test_model_tiers(monkeypatch):
    monkeypatch.delenv("AGFLOW_MODEL", raising=False)
    assert model_for_tier("pro") is None
    monkeypatch.setenv("AGFLOW_MODEL_FLASH", "fast")
    assert model_for_tier("flash") == "fast"


def test_node_result_schema():
    assert set(NodeResult.model_json_schema()["properties"]) == {"status", "summary", "artifacts", "followups"}
