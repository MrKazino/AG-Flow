"""The AG-Flow Manager inside Antigravity: sidecar files and status, the daily upkeep, the workflow view, the panel's
server (including the sidecar handshake) and the commands around them. Nothing here enables a sidecar or touches the
network; Antigravity and GitHub are replaced by fixtures."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from agflow import antigravity_probe as ap
from agflow import compat, improve, learn, maintenance, paths, plugin_files, sidecars, update, workflows
from agflow import install as inst
from agflow.cli import app as cli_app
from agflow.dashboard import app as dashboard
from agflow.graph import catalog, engine
from agflow.graph.store import known_workspaces, register_run
from agflow.release_feed import FeedError
from tests.conftest import PLUGIN, write

TOKEN = "t0ken"
NOW = time.time()
DAY = 86400.0
runner = CliRunner()
WF = """\
id: wf
defaults: {retries: 1, timeout_min: 10}
nodes:
  - id: spec
    agent: architect
    skill: plan-spec
    outputs: ["docs/spec.md"]
  - id: build
    agent: implementer
    depends_on: [spec]
  - id: review
    agent: reviewer
    depends_on: [build]
  - id: gate
    type: gate
    depends_on: [review]
    on_fail: {goto: build, max_loops: 1}
"""


@pytest.fixture
def installed(home):
    inst.install("global", with_gemini_md=False)
    return inst.plugin_target("global", None)


@pytest.fixture
def ws(workspace, graphs_dir):
    write(graphs_dir / "wf.yaml", WF)
    return workspace


def teach(ws, ledgers, *, status="succeeded"):
    graph = catalog.resolve("wf", ws)[0]
    for ledger in ledgers:
        state = engine.new_run(graph, {}, str(ws), now=NOW, run_id=f"wf-{len(learn.load_runs(ws, 'wf')):03d}")
        state.status = status  # type: ignore[assignment]
        learn.record_run(ws, state, ledger, now=NOW - DAY)


# --- the sidecar files ----------------------------------------------------------------------------


@pytest.mark.parametrize("data, ui, fragment", [
    ({"command": "agflow", "args": ["x"]}, False, None),
    ([], False, "must contain a JSON object"),
    ({}, False, "exactly one of"),
    ({"command": "a", "builtin": "schedule"}, False, "exactly one of"),
    ({"command": "a", "args": "x"}, False, "list of strings"),
    ({"command": "a", "args": [1]}, False, "list of strings"),
    ({"command": "a", "restart_policy": "sometimes"}, False, "restart_policy"),
    ({"builtin": "schedule", "args": []}, False, "five-field cron"),
    ({"builtin": "schedule", "args": ["* * *", "x"]}, False, "five-field cron"),
    ({"builtin": "schedule", "args": ["CRON_TZ=UTC 17 9 * * *", "agflow"]}, False, None),
    ({"command": "a"}, True, "has_web_ui"),
    ({"command": "a", "has_web_ui": True}, True, "ui_config.views"),
    ({"command": "a", "has_web_ui": True, "ui_config": {"views": [{"entrypoint": "OTHER", "path": "/"}]}}, True,
     "ui_config.views"),
])
def test_sidecar_json_checks(data, ui, fragment):
    found = sidecars.check_sidecar_json(data, ui=ui)
    assert (fragment is None and found == []) or (fragment is not None and any(fragment in p for p in found)), found


def test_the_bundled_sidecars_are_valid_and_shaped_for_this_machine_later():
    manager = json.loads((PLUGIN / "sidecars" / "manager" / "sidecar.json").read_text(encoding="utf-8"))
    daily = json.loads((PLUGIN / "sidecars" / "maintenance" / "sidecar.json").read_text(encoding="utf-8"))
    assert sidecars.check_sidecar_json(manager, ui=True) == [] and sidecars.check_sidecar_json(daily, ui=False) == []
    assert manager["command"] == "agflow" and manager["args"] == ["dashboard", "--sidecar"]
    assert manager["ui_config"]["views"][0]["path"] == "/"
    assert daily["builtin"] == "schedule" and daily["args"][1:] == ["agflow", "maintenance", "run"]
    assert "CRON_TZ=" in daily["args"][0]


def test_sidecar_files_are_managed_so_an_update_never_treats_them_as_your_edits():
    assert plugin_files.is_managed("sidecars/manager/sidecar.json")
    assert not plugin_files.is_managed("sidecars/manager/README.md")


def test_installing_points_each_sidecar_at_this_machines_agflow(installed, monkeypatch):
    manager = json.loads((installed / "sidecars" / "manager" / "sidecar.json").read_text(encoding="utf-8"))
    daily = json.loads((installed / "sidecars" / "maintenance" / "sidecar.json").read_text(encoding="utf-8"))
    cmd = inst.agflow_command()
    assert manager["command"] == cmd[0] and manager["args"] == [*cmd[1:], "dashboard", "--sidecar"]
    assert daily["args"] == ["CRON_TZ=UTC 17 9 * * *", cmd[0], *cmd[1:], "maintenance", "run"]
    assert Path(manager["command"]).is_absolute() or manager["command"] == cmd[0]
    assert sidecars.check_sidecar_json(manager, ui=True) == []


def test_a_module_launcher_is_split_into_command_and_arguments(home, monkeypatch):
    monkeypatch.setattr(inst, "agflow_command", lambda: ["C:/Py/python.exe", "-m", "agflow"])
    inst.install("global", with_gemini_md=False)
    target = inst.plugin_target("global", None)
    manager = json.loads((target / "sidecars" / "manager" / "sidecar.json").read_text(encoding="utf-8"))
    daily = json.loads((target / "sidecars" / "maintenance" / "sidecar.json").read_text(encoding="utf-8"))
    assert manager["command"] == "C:/Py/python.exe" and manager["args"] == ["-m", "agflow", "dashboard", "--sidecar"]
    assert daily["args"][1:] == ["C:/Py/python.exe", "-m", "agflow", "maintenance", "run"]


def test_an_install_without_the_sidecars_folder_still_works(home, monkeypatch, tmp_path):
    source = tmp_path / "plugin"
    import shutil
    shutil.copytree(PLUGIN, source)
    shutil.rmtree(source / "sidecars")
    inst.install("global", None, source=source, with_gemini_md=False)
    assert not (inst.plugin_target("global", None) / "sidecars").exists()
    assert [s.installed for s in sidecars.status()] == [False, False]


# --- what can be said about them ------------------------------------------------------------------


def test_status_distinguishes_missing_invalid_switched_off_and_ready(installed):
    assert [(s.id, s.installed, s.valid, s.enabled, s.ready) for s in sidecars.status()] == [
        ("agflow/manager", True, True, None, False), ("agflow/maintenance", True, True, None, False)]
    config = paths.antigravity_config_file()
    config.write_text(json.dumps({"sidecars": {"agflow/manager": {"enabled": True},
                                                "agflow/maintenance": {"enabled": False}}}), encoding="utf-8")
    manager, daily = sidecars.status()
    assert manager.ready and daily.enabled is False and not daily.ready
    (installed / "sidecars" / "manager" / "sidecar.json").write_text("{broken", encoding="utf-8")
    broken = sidecars.status()[0]
    assert broken.installed and not broken.valid and broken.problems == ["sidecar.json cannot be read"]
    (installed / "sidecars" / "manager" / "sidecar.json").unlink()
    assert not sidecars.status()[0].installed


@pytest.mark.parametrize("content", ["", "{broken", "[]", '{"sidecars": []}', '{"sidecars": {"agflow/manager": 1}}',
                                     '{"sidecars": {"agflow/manager": {"enabled": "yes"}}}'])
def test_an_unreadable_config_means_no_decision_not_an_error(installed, content):
    paths.antigravity_config_file().write_text(content, encoding="utf-8")
    assert sidecars.enabled_in_config("agflow/manager") is None
    assert sidecars.enabled_in_config("agflow/manager", paths.antigravity_config_file().parent / "none.json") is None


def test_agflow_never_writes_antigravitys_config(installed):
    config = paths.antigravity_config_file()
    config.write_text(json.dumps({"sidecars": {}, "userSettings": {"keep": "me"}}), encoding="utf-8")
    before = config.read_bytes()
    sidecars.status()
    sidecars.lines(sidecars.status())
    assert config.read_bytes() == before
    source = Path(sidecars.__file__).read_text(encoding="utf-8")
    assert ".write_text(" not in source and "write_json" not in source and "open(" not in source


def test_the_report_gives_the_user_the_steps_and_only_offers_the_link_once_it_is_on(installed):
    text = "\n".join(sidecars.lines(sidecars.status()))
    assert "installed, switched off" in text and "Customizations" in text
    assert '"agflow/manager": { "enabled": true }' in text
    assert "sidecar://" not in text
    paths.antigravity_config_file().write_text(json.dumps({"sidecars": {"agflow/manager": {"enabled": True}}}),
                                               encoding="utf-8")
    ready = "\n".join(sidecars.lines(sidecars.status()))
    assert "AG-Flow Manager (agflow/manager): ready" in ready and sidecars.PILL in ready
    assert sidecars.pill_link() == "[AG-Flow Manager](sidecar://agflow/manager/)"


# --- the daily upkeep -----------------------------------------------------------------------------


def test_upkeep_records_what_it_found_and_installs_nothing(home, tmp_path, monkeypatch):
    monkeypatch.setattr(maintenance.compat, "run", lambda directory=None: compat.Report(status="drift"))
    monkeypatch.setattr(maintenance.update, "check", lambda **kw: update.CheckResult(
        "available", "0.3.0", update.ReleaseInfo("0.9.0", "v0.9.0")))
    done = maintenance.run(directory=tmp_path, now=1234.0)
    assert (done["sync"], done["update"], done["latest"], done["errors"]) == ("drift", "available", "0.9.0", [])
    assert json.loads(maintenance.state_file(tmp_path).read_text(encoding="utf-8"))["at"] == 1234.0


def test_a_failing_check_is_recorded_not_raised(home, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(maintenance.compat, "run", boom)
    monkeypatch.setattr(maintenance.update, "check", lambda **kw: update.CheckResult("error", "0.3.0", error="offline"))
    done = maintenance.run(directory=tmp_path)
    assert done["sync"] == "unknown" and any("probe exploded" in e for e in done["errors"])
    assert any("offline" in e for e in done["errors"])


def test_upkeep_survives_an_unwritable_state_folder(home, tmp_path, monkeypatch):
    blocker = tmp_path / "state"
    blocker.write_text("a file", encoding="utf-8")
    monkeypatch.setattr(maintenance.compat, "run", lambda directory=None: compat.Report(status="ok"))
    monkeypatch.setattr(maintenance.update, "check", lambda **kw: update.CheckResult("up-to-date", "0.3.0"))
    assert maintenance.run(directory=blocker)["sync"] == "ok"


def test_health_is_cached_offline_and_names_what_needs_attention(home, tmp_path):
    empty = maintenance.health(tmp_path, now=1000.0)
    assert empty["compat"]["status"] is None and empty["maintenance"]["stale"] and empty["notes"] == []
    compat.save_last(compat.Report(status="breaking", agy_version="9.0.0"), tmp_path)
    state = {"checked_at": 5.0, "latest": update.ReleaseInfo("99.0.0", "v99.0.0").to_json()}
    update.save_state(state, tmp_path)
    maintenance.state_file(tmp_path).write_text(json.dumps({"at": 1000.0}), encoding="utf-8")
    got = maintenance.health(tmp_path, now=1000.0 + DAY)
    assert got["compat"]["status"] == "breaking" and got["update"]["available"] and got["update"]["latest"] == "99.0.0"
    assert not got["maintenance"]["stale"]
    assert got["notes"] == ["Antigravity breaking: run `agflow sync check`",
                            "AG-Flow 99.0.0 is available: run `agflow update plan`"]
    assert maintenance.health(tmp_path, now=1000.0 + 5 * DAY)["maintenance"]["stale"]


def test_health_with_a_damaged_cache_still_answers(home, tmp_path):
    maintenance.state_file(tmp_path).write_text("{broken", encoding="utf-8")
    update.state_dir(tmp_path).mkdir(parents=True, exist_ok=True)
    (update.state_dir(tmp_path) / update.STATE_NAME).write_text("[1]", encoding="utf-8")
    assert maintenance.health(tmp_path)["maintenance"]["last_run"] is None


# --- known workspaces -----------------------------------------------------------------------------


def test_known_workspaces_are_the_ones_with_runs_newest_first(home, tmp_path):
    a, b, gone = tmp_path / "a", tmp_path / "b", tmp_path / "gone"
    a.mkdir()
    b.mkdir()
    register_run("r1", a)
    register_run("r2", gone)
    register_run("r3", b)
    register_run("r4", a)
    assert known_workspaces() == [a, b]
    assert known_workspaces(1) == [a]


# --- the workflow view ----------------------------------------------------------------------------


def test_a_workflow_that_can_run_reports_what_it_needs(ws, installed):
    data = workflows.detail(ws, "wf")
    assert data["error"] == "" and [n["id"] for n in data["nodes"]] == ["spec", "build", "review", "gate"]
    assert data["needs"]["agents"]["architect"]["found"] and data["needs"]["agents"]["architect"]["nodes"] == ["spec"]
    assert data["needs"]["skills"]["plan-spec"]["enabled"] is True and data["needs"]["mcp"]["agflow"]["found"]
    [out] = data["needs"]["files"]
    assert out["path"] == "docs/spec.md" and out["exists"] is False and out["evidence"] == "advisory"
    assert [p["code"] for p in data["problems"]] == ["workflow.evidence.advisory"]
    write(ws / "docs" / "spec.md", "x")
    assert workflows.detail(ws, "wf")["needs"]["files"][0]["exists"] is True


def test_templated_file_paths_are_not_resolved(ws, installed, graphs_dir):
    write(graphs_dir / "wf.yaml", WF.replace('"docs/spec.md"', '"docs/{{ run.id }}.md"'))
    row = workflows.detail(ws, "wf")["needs"]["files"][0]
    assert row["templated"] is True and row["exists"] is None


def test_missing_and_disabled_things_are_problems_with_the_right_severity(ws, installed, graphs_dir):
    from agflow.inventory import Inventory
    from agflow.models import Kind
    from agflow.toggles import disable

    write(graphs_dir / "wf.yaml", WF.replace("agent: reviewer", "agent: ghost").replace("skill: plan-spec",
                                                                                         "skill: nonexistent"))
    disable(Inventory.discover(ws), Kind.AGENT, "architect")
    found = {p["code"]: p for p in workflows.detail(ws, "wf")["problems"]}
    assert found["workflow.agent.missing"]["level"] == "error" and "ghost" in found["workflow.agent.missing"]["message"]
    assert found["workflow.agent.disabled"]["level"] == "error"
    assert found["workflow.skill.missing"]["level"] == "warning"
    assert workflows.detail(ws, "wf")["problems"][0]["level"] == "error", "most serious first"


def test_without_the_orchestrator_server_the_workflow_says_chat_cannot_drive_it(ws, home):
    codes = [p["code"] for p in workflows.detail(ws, "wf")["problems"]]
    assert "workflow.mcp.orchestrator" in codes


def test_runs_lessons_and_proposals_appear_on_the_workflow(ws, installed):
    fail = {"event": "reported", "node": "build", "status": "failed", "summary": "tests did not compile"}
    teach(ws, [[fail], [fail]])
    teach(ws, [[]], status="failed")
    proposal = improve.propose(ws, "wf", now=NOW)
    data = workflows.detail(ws, "wf")
    assert data["runs"]["recorded"] == 3 and data["runs"]["success_rate"] == 0.667 and len(data["runs"]["recent"]) == 3
    assert data["lessons"][0]["kind"] == "failed_report" and data["lessons"][0]["runs"] == 2
    [shown] = data["proposals"]
    assert shown["id"] == proposal.id and shown["status"] == "pending" and shown["diff"] and not shown["creates_copy"]
    [row] = [r for r in workflows.summary(ws) if r["id"] == "wf"]
    assert row["proposals_pending"] == 1 and row["lessons"] == 2 and row["runs"]["recorded"] == 3


def test_the_summary_lists_every_workflow_and_marks_a_broken_one(ws, installed, graphs_dir):
    write(graphs_dir / "broken.yaml", "id: broken\nnodes: []\n")
    rows = {r["id"]: r for r in workflows.summary(ws)}
    assert {"bugfix", "feature-delivery", "wf"} <= set(rows)
    assert rows["wf"]["nodes"] == 4 and rows["wf"]["agents"] == ["architect", "implementer", "reviewer"]
    bad = next(r for r in rows.values() if r["error"])
    assert bad["nodes"] == 0 and bad["problems"]["error"] == 1
    assert workflows.detail(ws, bad["id"])["problems"][0]["code"] == "workflow.invalid"


def test_an_unknown_workflow_is_an_error_row_not_an_exception(ws):
    data = workflows.detail(ws, "no-such")
    assert "unknown" in data["error"] and data["problems"][0]["level"] == "error"


# --- the panel's server ---------------------------------------------------------------------------


@pytest.fixture
def client(ws, installed):
    c = TestClient(dashboard.create_app(ws, TOKEN))
    c.headers["X-Agflow-Token"] = TOKEN
    return c


def test_inside_antigravity_the_panel_also_accepts_the_sidecar_token_header(ws, installed):
    plain = TestClient(dashboard.create_app(ws, TOKEN))
    pane = TestClient(dashboard.create_app(ws, TOKEN, sidecar=True))
    assert plain.get("/api/health", headers={"x-sidecar-token": TOKEN}).status_code == 401
    assert pane.get("/api/health", headers={"x-sidecar-token": TOKEN}).status_code == 200
    assert pane.get("/api/health", headers={"x-sidecar-token": "wrong"}).status_code == 401
    assert pane.get("/api/health").status_code == 401
    assert pane.get("/api/health", params={"token": TOKEN}).status_code == 401, "never a token in the URL"
    # the page shell loads from the pane's address, which carries the token and the conversation
    assert pane.get("/", params={"token": TOKEN, "conversationId": "c"}).status_code == 200


def test_the_handshake_comes_from_antigravitys_environment():
    env = {"ANTIGRAVITY_SIDECAR_WEB_PORT": "43123", "ANTIGRAVITY_SIDECAR_UI_TOKEN": " tok "}
    assert dashboard.sidecar_settings(env) == (43123, "tok")
    last_resort = {"ANTIGRAVITY_SIDECAR_WEB_PORT": "1", "ANTIGRAVITY_SIDECAR_WEB_TOKEN": "w"}
    assert dashboard.sidecar_settings(last_resort) == (1, "w")
    both = {**env, "ANTIGRAVITY_SIDECAR_AUTH_TOKEN": "other"}
    assert dashboard.sidecar_settings(both)[1] == "tok", "the UI token wins, as in Antigravity's own runtime"


@pytest.mark.parametrize("env, fragment", [
    ({}, "ANTIGRAVITY_SIDECAR_WEB_PORT is not set"),
    ({"ANTIGRAVITY_SIDECAR_WEB_PORT": "abc", "ANTIGRAVITY_SIDECAR_UI_TOKEN": "t"}, "not set"),
    ({"ANTIGRAVITY_SIDECAR_WEB_PORT": "0", "ANTIGRAVITY_SIDECAR_UI_TOKEN": "t"}, "not set"),
    ({"ANTIGRAVITY_SIDECAR_WEB_PORT": "70000", "ANTIGRAVITY_SIDECAR_UI_TOKEN": "t"}, "not set"),
    ({"ANTIGRAVITY_SIDECAR_WEB_PORT": "5000"}, "refusing to serve an unauthenticated manager"),
    ({"ANTIGRAVITY_SIDECAR_WEB_PORT": "5000", "ANTIGRAVITY_SIDECAR_UI_TOKEN": "  "}, "unauthenticated"),
])
def test_without_the_handshake_the_panel_refuses_to_start(env, fragment):
    with pytest.raises(dashboard.SidecarError, match=fragment):
        dashboard.sidecar_settings(env)


def test_the_panel_listens_on_loopback_only_on_antigravitys_port(ws, monkeypatch):
    import uvicorn

    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda application, **kw: seen.update(kw, app=application))
    monkeypatch.setenv("ANTIGRAVITY_SIDECAR_WEB_PORT", "43999")
    monkeypatch.setenv("ANTIGRAVITY_SIDECAR_UI_TOKEN", "pane-token")
    dashboard.serve_sidecar(ws)
    assert (seen["host"], seen["port"]) == ("127.0.0.1", 43999)
    c = TestClient(seen["app"])
    assert c.get("/api/health", headers={"x-sidecar-token": "pane-token"}).status_code == 200


def test_the_panel_starts_on_the_workspace_that_was_most_recently_used(home, tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    register_run("r1", a)
    register_run("r2", b)
    monkeypatch.delenv("AGFLOW_WORKSPACE", raising=False)
    assert dashboard.sidecar_workspace({}) == b
    assert dashboard.sidecar_workspace({"AGFLOW_WORKSPACE": str(a)}) == a
    assert dashboard.sidecar_workspace({"AGFLOW_WORKSPACE": str(tmp_path / "missing")}) == b
    assert dashboard.sidecar_workspace({}, hint=a) == a


def test_the_workspace_can_be_switched_from_the_page(client, ws, tmp_path, home):
    other = tmp_path / "other"
    other.mkdir()
    register_run("r1", other)
    got = client.get("/api/workspaces").json()
    assert got["current"] == str(ws.resolve()) and str(other) in got["known"] and got["sidecar"] is False
    assert client.post("/api/workspace", json={"path": "relative/path"}).status_code == 400
    assert client.post("/api/workspace", json={"path": str(tmp_path / "nowhere")}).status_code == 400
    assert client.post("/api/workspace", json={"path": str(other)}).json() == {"workspace": str(other.resolve())}
    assert client.get("/api/overview").json()["workspace"] == str(other.resolve())
    assert "wf" not in {w["id"] for w in client.get("/api/workflows").json()}


def test_workflow_endpoints(client, ws):
    listed = {w["id"]: w for w in client.get("/api/workflows").json()}
    assert "wf" in listed and listed["wf"]["nodes"] == 4
    detail = client.get("/api/workflows/wf").json()
    assert detail["id"] == "wf" and detail["needs"]["agents"]["architect"]["found"]
    assert client.get("/api/workflows/no-such").status_code == 404
    assert client.post("/api/workflows/%2e%2e/propose").status_code in (400, 404, 405)


def test_the_proposal_cycle_through_the_page_api(client, ws):
    fail = {"event": "reported", "node": "build", "status": "failed", "summary": "tests did not compile"}
    teach(ws, [[fail], [fail]])
    made = client.post("/api/workflows/wf/propose").json()["proposal"]
    assert made["status"] == "pending" and made["changes"][0]["text"].startswith("build: retries: 1 -> 2")
    assert client.post("/api/workflows/wf/propose").json()["proposal"]["id"] == made["id"]
    assert client.post("/api/proposals/nope/apply", json={}).status_code == 400
    applied = client.post(f"/api/proposals/{made['id']}/apply", json={"only": ["c1"]}).json()["proposal"]
    assert applied["status"] == "applied"
    assert catalog.resolve("wf", ws)[0].node("build").retries == 2
    assert client.post(f"/api/proposals/{made['id']}/apply", json={}).status_code == 400
    undone = client.post("/api/workflows/wf/undo", json={"force": False}).json()["proposal"]
    assert undone["status"] == "undone" and catalog.resolve("wf", ws)[0].node("build").retries is None
    assert client.post("/api/workflows/wf/undo", json={}).status_code == 400


def test_declining_a_proposal_through_the_page_api(client, ws):
    fail = {"event": "reported", "node": "build", "status": "failed", "summary": "tests did not compile"}
    teach(ws, [[fail], [fail]])
    made = client.post("/api/workflows/wf/propose").json()["proposal"]
    declined = client.post(f"/api/proposals/{made['id']}/reject", json={"reason": "not now"}).json()["proposal"]
    assert declined["status"] == "rejected"
    assert client.post(f"/api/proposals/{made['id']}/reject", json={}).status_code == 400
    again = client.post("/api/workflows/wf/propose").json()["proposal"]
    assert again is None, "declined: not proposed again for a month"


def verified_snapshot() -> ap.Snapshot:
    return ap.Snapshot.from_json(compat.shipped()["verified"])


def test_health_sync_and_accept_through_the_page_api(client, ws, monkeypatch):
    snap = verified_snapshot()
    monkeypatch.setattr(ap, "probe", lambda *a, **k: snap)
    assert client.get("/api/health").json()["sync"] is None
    assert client.post("/api/sync/check").json()["status"] == "ok"
    health = client.get("/api/health").json()
    assert health["sync"]["status"] == "ok" and health["sidecar_mode"] is False and health["notes"] == []
    assert [s["id"] for s in health["sidecars"]] == ["agflow/manager", "agflow/maintenance"]
    assert health["sidecars"][0]["steps"] and health["pill"].startswith("[AG-Flow Manager](sidecar://")

    drifted = verified_snapshot()
    drifted.markers["agy-customizations/docs/hooks.md"]["table_keys"].append("PreCompaction")
    drifted.doc_hashes["agy-customizations/docs/hooks.md"] = "0" * 64
    monkeypatch.setattr(ap, "probe", lambda *a, **k: drifted)
    monkeypatch.setattr(compat, "installation_problems", lambda workspace=None: [])
    assert client.post("/api/sync/check").json()["status"] == "drift"
    assert client.get("/api/health").json()["notes"] == ["Antigravity drift: run `agflow sync check`"]
    dry = client.post("/api/sync/apply", json={"dry_run": True}).json()
    assert dry["would_accept"] and not dry["accepted"] and not compat.accepted_path().exists()
    done = client.post("/api/sync/apply", json={"dry_run": False}).json()
    assert done["accepted"] and compat.accepted_path().is_file()


def test_the_page_cannot_accept_when_the_installation_has_errors(client, monkeypatch):
    drifted = verified_snapshot()
    drifted.doc_hashes["agy-customizations/docs/hooks.md"] = "0" * 64
    drifted.markers["agy-customizations/docs/hooks.md"]["fields"].append("brandNewField")
    monkeypatch.setattr(ap, "probe", lambda *a, **k: drifted)
    monkeypatch.setattr(compat, "installation_problems", lambda workspace=None: ["hooks.broken: the Stop hook crashed"])
    refused = client.post("/api/sync/apply", json={"dry_run": False}).json()
    assert not refused["accepted"] and not refused["would_accept"] and refused["problems"]
    assert not compat.accepted_path().exists()


def test_update_endpoints(client, monkeypatch):
    monkeypatch.setattr(update, "check", lambda **kw: update.CheckResult(
        "available", "0.3.0", update.ReleaseInfo("0.9.0", "v0.9.0")) if kw.get("force") else update.CheckResult(
        "up-to-date", "0.3.0"))
    assert client.post("/api/updates/check", json={"force": True}).json()["status"] == "available"
    assert client.post("/api/updates/check", json={"force": False}).json()["status"] == "up-to-date"

    def refuse(**kw):
        raise FeedError("nothing is published yet")

    monkeypatch.setattr(update, "plan", refuse)
    assert client.get("/api/updates/plan").json() == {"plan": None, "error": "nothing is published yet"}
    mode = update.InstallMode("venv", Path("/x/agflow"), Path("/x"), "python")
    plan = update.Plan("0.3.0", "0.9.0", "v0.9.0", "minor", "notes", "w.whl", 1, mode, steps=list(update.STEPS))
    monkeypatch.setattr(update, "plan", lambda **kw: plan)
    got = client.get("/api/updates/plan").json()["plan"]
    assert got["target"] == "0.9.0" and got["ok"] is True and len(got["steps"]) == 6


def test_every_new_endpoint_needs_the_token(ws, installed):
    bare = TestClient(dashboard.create_app(ws, TOKEN))
    for method, path in [("get", "/api/workspaces"), ("post", "/api/workspace"), ("get", "/api/workflows"),
                         ("get", "/api/workflows/wf"), ("post", "/api/workflows/wf/propose"),
                         ("post", "/api/proposals/x/apply"), ("get", "/api/health"), ("post", "/api/sync/check"),
                         ("post", "/api/sync/apply"), ("post", "/api/updates/check"), ("get", "/api/updates/plan")]:
        assert getattr(bare, method)(path).status_code == 401, path


# --- the commands ---------------------------------------------------------------------------------


def cli(*args):
    return runner.invoke(cli_app, list(args), catch_exceptions=False)


def test_panel_status_and_link(installed):
    out = cli("panel", "status")
    assert out.exit_code == 0 and "AG-Flow Manager (agflow/manager): installed, switched off" in out.output
    data = json.loads(cli("panel", "status", "--json").output)
    assert [d["ready"] for d in data] == [False, False]
    assert cli("panel", "link").output.strip() == "[AG-Flow Manager](sidecar://agflow/manager/)"


def test_the_maintenance_command(home, monkeypatch):
    monkeypatch.setattr(maintenance.compat, "run", lambda directory=None: compat.Report(status="ok"))
    monkeypatch.setattr(maintenance.update, "check", lambda **kw: update.CheckResult("up-to-date", "0.3.0"))
    assert "sync: ok; update: up-to-date" in cli("maintenance", "run").output
    assert json.loads(cli("maintenance", "run", "--json").output)["sync"] == "ok"
    assert cli("maintenance", "sleep").exit_code == 1


def test_the_workflows_command(ws, installed):
    table = cli("workflows", "-w", str(ws))
    assert table.exit_code == 0 and "wf" in table.output and "feature-delivery" in table.output
    assert json.loads(cli("workflows", "-w", str(ws), "--json").output)[0]["id"]
    one = cli("workflows", "wf", "-w", str(ws)).output
    assert "wf (workspace)" in one and "agent  architect" in one and "no finished runs" in one
    assert cli("workflows", "no-such", "-w", str(ws)).exit_code == 1
    assert json.loads(cli("workflows", "wf", "-w", str(ws), "--json").output)["id"] == "wf"


def test_the_sidecar_flag_explains_itself_outside_antigravity(monkeypatch):
    for name in ("ANTIGRAVITY_SIDECAR_WEB_PORT", "ANTIGRAVITY_SIDECAR_UI_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    out = cli("dashboard", "--sidecar")
    assert out.exit_code == 1 and "started by Antigravity" in out.output


def test_the_sidecar_flag_hands_over_to_the_panel_server(ws, monkeypatch):
    seen = {}
    monkeypatch.setattr(dashboard, "serve_sidecar", lambda workspace=None: seen.update(workspace=workspace))
    assert cli("dashboard", "--sidecar", "-w", str(ws)).exit_code == 0
    assert seen["workspace"] == ws.resolve()
    assert os.environ.get("ANTIGRAVITY_SIDECAR_UI_TOKEN") is None
