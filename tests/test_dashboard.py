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
    # F1: the bearer token is only ever accepted in the header. A token in the URL ends up in browser history and in
    # every proxy/server log, and whoever holds that URL has full dashboard control.
    assert c.get("/api/overview", params={"token": TOKEN}).status_code == 401
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


# --- F1: the live stream authenticates with a one-time ticket, never with the long-lived token in the URL -----------


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def live(workspace, clock):
    """A client with a run in progress, plus helpers for tickets."""
    g, p = resolve("bugfix", workspace)
    state = RunStore(workspace).create(g, coerce_inputs(g, {"bug": "x"}), graph_path=str(p))
    c = TestClient(create_app(workspace, TOKEN, ticket_ttl=30.0, clock=clock, stream_seconds=0.3, stream_poll=0.05))
    return c, state.run_id


def get_ticket(c: TestClient, run_id: str, **kw) -> dict:
    return c.post("/api/stream-ticket", json={"run_id": run_id}, headers={"X-Agflow-Token": TOKEN}, **kw)


def read_first_frame(c: TestClient, run_id: str, ticket: str) -> str:
    with c.stream("GET", f"/api/runs/{run_id}/stream", params={"ticket": ticket}) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("data:"):
                return line
    raise AssertionError("no data frame")


@pytest.mark.parametrize("path", ["/api/overview", "/api/doctor", "/api/items", "/api/runs", "/api/graphs",
                                  "/api/mcp", "/api/profiles", "/api/fix/plan", "/api/fix/history", "/api/gemini-md"])
def test_query_token_is_rejected_on_every_route(workspace, path):
    c = TestClient(create_app(workspace, TOKEN))
    assert c.get(path, params={"token": TOKEN}).status_code == 401
    assert c.get(path, headers={"X-Agflow-Token": TOKEN}).status_code in (200, 404)  # header still works


def test_query_token_is_rejected_on_the_stream_route(live):
    c, run_id = live
    assert c.get(f"/api/runs/{run_id}/stream", params={"token": TOKEN}).status_code == 401


def test_ticket_requires_the_header_token(live):
    c, run_id = live
    assert c.post("/api/stream-ticket", json={"run_id": run_id}).status_code == 401
    assert c.post("/api/stream-ticket", json={"run_id": run_id}, headers={"X-Agflow-Token": "wrong"}).status_code == 401
    assert c.post("/api/stream-ticket", json={"run_id": run_id}, params={"token": TOKEN}).status_code == 401
    ok = get_ticket(c, run_id)
    assert ok.status_code == 200 and ok.json()["expires_in"] == 30.0 and len(ok.json()["ticket"]) >= 20


def test_ticket_for_an_unknown_run_is_refused(live):
    c, _ = live
    assert get_ticket(c, "no-such-run").status_code == 404
    auth = {"X-Agflow-Token": TOKEN}
    assert c.post("/api/stream-ticket", json={"run_id": "../escape"}, headers=auth).status_code == 404
    assert c.post("/api/stream-ticket", json={}, headers=auth).status_code == 400


def test_stream_connects_with_a_ticket(live):
    c, run_id = live
    frame = read_first_frame(c, run_id, get_ticket(c, run_id).json()["ticket"])
    assert run_id in frame


def test_ticket_is_single_use(live):
    c, run_id = live
    ticket = get_ticket(c, run_id).json()["ticket"]
    read_first_frame(c, run_id, ticket)
    assert c.get(f"/api/runs/{run_id}/stream", params={"ticket": ticket}).status_code == 401


def test_ticket_expires(live, clock):
    c, run_id = live
    ticket = get_ticket(c, run_id).json()["ticket"]
    clock.now += 31.0
    assert c.get(f"/api/runs/{run_id}/stream", params={"ticket": ticket}).status_code == 401


def test_ticket_is_bound_to_its_run_and_to_the_stream_route(workspace, live):
    c, run_id = live
    g, p = resolve("release", workspace)
    other = RunStore(workspace).create(g, coerce_inputs(g, {"version": "1.0.0"}), graph_path=str(p))
    ticket = get_ticket(c, run_id).json()["ticket"]
    assert c.get(f"/api/runs/{other.run_id}/stream", params={"ticket": ticket}).status_code == 401
    # a ticket is not a credential for anything else
    again = get_ticket(c, run_id).json()["ticket"]
    assert c.get("/api/overview", params={"ticket": again}).status_code == 401
    assert c.get(f"/api/runs/{run_id}", params={"ticket": again}).status_code == 401


def test_a_wrong_ticket_is_rejected(live):
    c, run_id = live
    assert c.get(f"/api/runs/{run_id}/stream", params={"ticket": "not-a-ticket"}).status_code == 401
    assert c.get(f"/api/runs/{run_id}/stream", params={"ticket": ""}).status_code == 401


def test_ticket_store_is_bounded_and_sweeps_expired_tickets(live, clock):
    c, run_id = live
    codes = [get_ticket(c, run_id).status_code for _ in range(300)]
    assert codes.count(200) == 256 and 429 in codes
    clock.now += 31.0  # all expired: room again
    assert get_ticket(c, run_id).status_code == 200


def test_the_dashboard_script_never_puts_a_token_in_a_url():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "src" / "agflow" / "dashboard" / "static" / "app.js").read_text(
        encoding="utf-8")
    assert "?token=" not in js and "&token=" not in js
    assert "stream-ticket" in js and "ticket=" in js


# --- F29: one palette, one table builder, one undo ----------------------------------------------------------------


def _static(name: str) -> str:
    from pathlib import Path

    return (Path(__file__).resolve().parents[1] / "src" / "agflow" / "dashboard" / "static" / name).read_text(
        encoding="utf-8")


def test_no_duplicate_dark_palette():
    """The dark colours used to be written twice (system preference and the manual switch) and had to be edited in
    both places; every theme colour is now one ``light-dark()`` token, written once."""
    import re

    css = _static("app.css")
    for dark_hex in ("#0f1017", "#171824", "#e8e9f3", "#8b85ff", "#5ccf85", "#ff7b72"):
        assert css.count(dark_hex) == 1, f"{dark_hex} appears {css.count(dark_hex)} times in app.css"
    assert "prefers-color-scheme" not in css, "the theme follows color-scheme; no media-query copy of the palette"
    assert len(re.findall(r"--[\w-]+:\s*light-dark\(", css)) >= 18
    # the switch forces a scheme and nothing else
    for theme in ("light", "dark"):
        block = re.search(rf':root\[data-theme="{theme}"\]\s*\{{([^}}]*)\}}', css)
        assert block and block.group(1).strip() == f"color-scheme: {theme};"
    assert "color-scheme: light dark;" in css


def test_the_rules_page_is_built_from_the_shared_component_table():
    js = _static("app.js")
    rules = js[js.index("pages.rules"):js.index("pages.mcp")]
    assert 'componentTable("rule"' in rules
    assert "toggleSwitch(" not in rules, "the rules page must not carry its own copy of the enable switch"
    assert js.count("/toggle`, { method: \"POST\"") >= 2  # the shared table plus the MCP page
    assert js.count("function componentTable(") == 1 and js.count("function componentPage(") == 1


def test_one_undo_function_serves_both_undo_buttons():
    js = _static("app.js")
    assert js.count('api("/fix/undo"') == 1, "the undo request must be written once"
    assert "function undoFix(" in js and "undoLast" not in js
    assert js.count("undoFix(") >= 3  # the definition and the two buttons


def test_a_real_migration_from_the_dashboard_is_undoable_and_a_dry_run_is_not_recorded(client, workspace):
    """N3: the page's migrate button used to move and rename files with no way back."""
    from tests.conftest import write

    wf = write(workspace / ".agents" / "workflows" / "ship.md", "---\ndescription: Ship it\n---\n1. Ship\n")
    dry = client.post("/api/migrate", json={"dry_run": True}).json()
    assert dry["actions"] and wf.exists() and client.get("/api/fix/plan").json()["last_batch"] is None
    real = client.post("/api/migrate", json={"dry_run": False}).json()
    assert real["actions"] and not wf.exists()
    assert (workspace / ".agents" / "skills" / "ship" / "SKILL.md").is_file()
    assert client.get("/api/fix/plan").json()["last_batch"]["fixes"] == ["agflow migrate"]
    assert client.post("/api/fix/undo").status_code == 200
    assert wf.is_file() and not (workspace / ".agents" / "skills" / "ship").exists()
