import pytest
from fastapi.testclient import TestClient

from agflow.dashboard.app import create_app
from agflow.graph.catalog import resolve
from agflow.graph.schema import coerce_inputs
from agflow.graph.store import RunStore
from agflow.install import install

TOKEN = "t0ken"


@pytest.fixture
def client(workspace):
    install("global", with_gemini_md=True)
    c = TestClient(create_app(workspace, TOKEN))
    c.headers["X-Agflow-Token"] = TOKEN
    return c


def test_auth_and_host_checks(workspace):
    c = TestClient(create_app(workspace, TOKEN))
    assert c.get("/api/overview").status_code == 401
    assert c.get("/api/overview", headers={"X-Agflow-Token": "wrong"}).status_code == 401
    assert c.get("/api/overview", params={"token": TOKEN}).status_code == 200
    assert c.get("/api/overview", headers={"X-Agflow-Token": TOKEN, "Host": "evil.example"}).status_code == 403
    assert c.get("/").status_code == 200  # the static shell itself holds no data


def test_overview_and_doctor(client):
    o = client.get("/api/overview").json()
    assert o["counts"]["skill"]["enabled"] == 20 and o["install"]["global"]["installed"]
    assert client.get("/api/doctor").json()["counts"]["error"] == 0


def test_items_file_and_toggle(client, workspace):
    skills = client.get("/api/items", params={"kind": "skill"}).json()
    tdd = next(s for s in skills if s["name"] == "tdd")
    f = client.get("/api/file", params={"path": tdd["path"]}).json()
    assert "red-green-refactor" in f["content"]
    assert client.get("/api/file", params={"path": "/etc/passwd"}).status_code == 404
    r = client.post("/api/items/skill/tdd/toggle", json={"enabled": False})
    assert r.status_code == 200
    assert not any(s["name"] == "tdd" and s["enabled"] for s in client.get("/api/items?kind=skill").json())
    assert client.post("/api/items/skill/tdd/toggle", json={"enabled": True}).status_code == 200


def test_gemini_md_roundtrip(client):
    g = client.get("/api/gemini-md").json()
    assert "Expert Senior Workflows Manager" in g["content"]
    assert client.put("/api/gemini-md", json={"content": "x" * 12_001}).status_code == 400
    assert client.put("/api/gemini-md", json={"content": g["content"] + "\n- extra\n"}).json()["ok"]


def test_mcp_endpoints(client):
    assert client.post("/api/mcp", json={"name": "github", "scope": "workspace"}).status_code == 200
    servers = {s["name"]: s for s in client.get("/api/mcp").json()["servers"]}
    assert servers["github"]["missing_env"] == ["GITHUB_PERSONAL_ACCESS_TOKEN"]
    assert not servers["agflow"]["editable"]  # provided by the plugin
    assert client.post("/api/mcp/workspace/github/toggle", json={"enabled": False}).status_code == 200
    assert client.delete("/api/mcp/workspace/github").status_code == 200
    assert client.post("/api/mcp", json={"name": "zzz"}).status_code == 400


def test_graph_endpoints(client, workspace):
    graphs = {g["id"] for g in client.get("/api/graphs").json()}
    assert {"feature-delivery", "bugfix"} <= graphs
    d = client.get("/api/graphs/bugfix").json()
    assert d["layout"]["nodes"][0]["layer"] == 0 and any(e["kind"] == "rework" for e in d["layout"]["edges"])
    bad = client.post("/api/graphs/validate", json={"yaml": "id: x\nnodes: []"}).json()
    assert bad["ok"] is False
    yaml = d["yaml"].replace("id: bugfix", "id: my-bugfix", 1)
    assert client.put("/api/graphs/my-bugfix", json={"yaml": yaml}).json()["ok"]
    assert (workspace / ".agents" / "agflow" / "graphs" / "my-bugfix.yaml").is_file()
    assert client.put("/api/graphs/other", json={"yaml": yaml}).status_code == 400


def test_run_endpoints(client, workspace):
    g, p = resolve("release", workspace)
    store = RunStore(workspace)
    state = store.create(g, coerce_inputs(g, {"version": "1.0.0"}), graph_path=str(p))
    runs = client.get("/api/runs").json()
    assert runs[0]["run_id"] == state.run_id
    d = client.get(f"/api/runs/{state.run_id}").json()
    assert d["status"] == "running" and len(d["layout"]["nodes"]) == 6
    not_waiting = client.post(f"/api/runs/{state.run_id}/approve", json={"node": "approve"})
    assert not_waiting.status_code == 400
    assert client.post(f"/api/runs/{state.run_id}/abort", json={"reason": "test"}).json()["ok"]
    assert client.get(f"/api/runs/{state.run_id}").json()["status"] == "aborted"
    assert client.get("/api/runs/nope").status_code == 404


def test_profiles_and_install(client, workspace):
    profiles = client.get("/api/profiles").json()
    assert len(profiles["full"]["skills"]) == 20
    refused = client.post("/api/install", json={"scope": "workspace", "profile": "core", "dry_run": True})
    assert refused.status_code == 400 and "already installed globally" in refused.json()["detail"]
    r = client.post("/api/install", json={"scope": "workspace", "profile": "core", "dry_run": True,
                                          "allow_duplicate": True}).json()
    assert r["ok"] and r["actions"]
    assert client.post("/api/install", json={"scope": "workspace", "profile": "nope"}).status_code == 400


def test_fix_endpoints(client, workspace):
    from tests.conftest import write

    rule = write(workspace / ".agents" / "rules" / "a.md", "Alpha rule.\n")
    wf = write(workspace / ".agents" / "workflows" / "ship.md", "---\ndescription: Ship it\n---\n1. Ship\n")
    plan = client.get("/api/fix/plan").json()
    assert plan["last_batch"] is None and plan["counts"]["error"] == 1
    by_code = {f["code"]: f for f in plan["fixes"]}
    assert by_code["workflow.deprecated"]["tier"] == "safe" and by_code["frontmatter.missing"]["tier"] == "review"
    assert client.post("/api/fix/apply", json={"selections": {}}).status_code == 400
    assert client.post("/api/fix/apply", json={"selections": ["x"]}).status_code == 400

    rid = by_code["frontmatter.missing"]["id"]
    r = client.post("/api/fix/apply", json={"selections": {by_code["workflow.deprecated"]["id"]: None,
                                                            rid: "always_on"}}).json()
    assert len(r["applied"]) == 2 and r["before"]["error"] == 1 and r["after"]["error"] == 0 and r["backup_dir"]
    assert not wf.exists() and "always_on" in rule.read_text()
    assert client.get("/api/doctor").json()["counts"]["error"] == 0
    assert client.get("/api/fix/plan").json()["last_batch"]["id"] == r["batch"]
    assert len(client.get("/api/fix/history").json()) == 1

    u = client.post("/api/fix/undo").json()
    assert len(u["fixes"]) == 2 and wf.is_file() and rule.read_text() == "Alpha rule.\n"
    assert client.post("/api/fix/undo").status_code == 400
