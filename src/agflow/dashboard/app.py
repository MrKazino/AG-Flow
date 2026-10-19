"""agflow dashboard: a local, token-protected web UI over the same modules the CLI uses."""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import threading
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agflow import __version__, hook_health, mcp_registry, paths, toggles
from agflow.fsutil import atomic_write_text, backup, timestamp
from agflow.graph import catalog, engine
from agflow.graph.render import to_mermaid
from agflow.graph.schema import GraphError, parse_graph_text
from agflow.graph.store import RunNotFound, RunStore
from agflow.inventory import Inventory
from agflow.models import Kind
from agflow.validate import summarize, validate_inventory

STATIC = Path(__file__).parent / "static"
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]", "testserver"}
TOKEN_HEADER = "x-agflow-token"
SIDECAR_TOKEN_HEADER = "x-sidecar-token"   # what Antigravity's own sidecar UIs send; accepted in sidecar mode
SIDECAR_PORT_ENV = "ANTIGRAVITY_SIDECAR_WEB_PORT"
SIDECAR_TOKEN_ENVS = ("ANTIGRAVITY_SIDECAR_UI_TOKEN", "ANTIGRAVITY_SIDECAR_AUTH_TOKEN", "ANTIGRAVITY_SIDECAR_WEB_TOKEN")
STREAM_PATH = re.compile(r"^/api/runs/([^/]+)/stream$")
TICKET_TTL_SECONDS = 30.0
TICKET_MAX = 256


class TicketStore:
    """Single-use, short-lived credentials for the live run stream.

    ``EventSource`` cannot send headers, and the long-lived dashboard token must never travel in a URL (browser
    history, proxy and server logs). The page therefore POSTs for a ticket (authenticated by the header), then
    opens the stream with ``?ticket=``. A ticket works once, for one run, for ``ttl`` seconds, and for nothing else.
    """

    def __init__(self, ttl: float, clock: Callable[[], float]):
        self.ttl, self.clock = ttl, clock
        self._tickets: dict[str, tuple[float, str]] = {}  # ticket -> (expires_at, run_id)
        self._lock = threading.Lock()

    def issue(self, run_id: str) -> str | None:
        """A new ticket, or ``None`` when too many unexpired ones are outstanding."""
        with self._lock:
            now = self.clock()
            self._tickets = {t: v for t, v in self._tickets.items() if v[0] > now}
            if len(self._tickets) >= TICKET_MAX:
                return None
            ticket = secrets.token_urlsafe(16)
            self._tickets[ticket] = (now + self.ttl, run_id)
            return ticket

    def redeem(self, ticket: str, run_id: str) -> bool:
        with self._lock:
            entry = self._tickets.pop(ticket, None)  # consumed whether or not it turns out to be valid
        return entry is not None and entry[1] == run_id and entry[0] > self.clock()


def graph_layout(graph: Any) -> dict[str, Any]:
    """Nodes/edges for the SVG renderer: layer = longest path from a root."""
    layer: dict[str, int] = {}
    for n in graph.nodes:  # nodes are validated acyclic; resolve lazily
        def depth(nid: str) -> int:
            if nid not in layer:
                deps = graph.node(nid).depends_on
                layer[nid] = 0 if not deps else 1 + max(depth(d) for d in deps)
            return layer[nid]

        depth(n.id)
    return {
        "id": graph.id,
        "title": graph.title,
        "description": graph.description,
        "inputs": {k: v.model_dump() for k, v in graph.inputs.items()},
        "nodes": [{"id": n.id, "type": n.type, "title": n.label, "agent": n.agent, "skill": n.skill,
                   "when": n.when, "layer": layer[n.id]} for n in graph.nodes],
        "edges": [{"from": d, "to": n.id, "kind": "dep"} for n in graph.nodes for d in n.depends_on]
        + [{"from": n.id, "to": n.on_fail.goto, "kind": "rework", "max_loops": n.on_fail.max_loops}
           for n in graph.nodes if n.on_fail],
    }


def create_app(workspace: Path, token: str, *, ticket_ttl: float = TICKET_TTL_SECONDS,
               clock: Callable[[], float] = time.monotonic, stream_seconds: float = 3600.0,
               stream_poll: float = 1.0, sidecar: bool = False) -> FastAPI:
    """``ticket_ttl``/``clock`` make stream-ticket expiry testable; ``stream_seconds``/``stream_poll`` bound how long
    one live stream lasts and how often it checks the run (tests shorten them so a stream ends by itself).
    ``sidecar``: running inside Antigravity's side pane, where the token comes from Antigravity and may also arrive as
    its own ``x-sidecar-token`` header."""
    app = FastAPI(title="agflow dashboard", version=__version__, docs_url=None, redoc_url=None)
    ws = workspace.resolve()
    tickets = TicketStore(ticket_ttl, clock)

    @app.middleware("http")
    async def guard(request: Request, call_next: Any) -> Any:
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        if host not in ALLOWED_HOSTS:
            return JSONResponse({"detail": "forbidden host"}, status_code=403)
        path = request.url.path
        if path.startswith("/api/"):
            stream = STREAM_PATH.match(path)
            ticket = request.query_params.get("ticket")
            if stream and ticket is not None:
                # the only route that accepts a URL credential, and only a one-time ticket for exactly this run
                if not tickets.redeem(ticket, stream.group(1)):
                    return JSONResponse({"detail": "missing, expired or already used stream ticket"}, status_code=401)
            else:
                given = request.headers.get(TOKEN_HEADER, "") or (
                    request.headers.get(SIDECAR_TOKEN_HEADER, "") if sidecar else "")
                if not secrets.compare_digest(given.encode("utf-8"), token.encode("utf-8")):
                    return JSONResponse({"detail": "missing or invalid token"}, status_code=401)
        return await call_next(request)

    def inventory() -> Inventory:
        return Inventory.discover(ws)

    # -- overview & doctor -------------------------------------------------------------------
    @app.get("/api/overview")
    def overview() -> dict[str, Any]:
        from agflow.install import status as install_status

        inv = inventory()
        issues = validate_inventory(inv)
        return {
            "version": __version__,
            "workspace": str(ws),
            "install": install_status(ws),
            "counts": {k.value: {"enabled": len(inv.of(k)), "disabled": len(inv.of(k, enabled=False))}
                       for k in Kind},
            "doctor": summarize(issues),
            "active_runs": [_run_brief(r) for r in RunStore(ws).active()],
            "gemini_md_limit": paths.GEMINI_MD_CHAR_LIMIT,
            "hook_health": hook_health.summary(),  # None while the safety hooks understand everything they receive
        }

    @app.get("/api/doctor")
    def doctor() -> dict[str, Any]:
        issues = validate_inventory(inventory())
        return {"counts": summarize(issues), "issues": [i.to_dict() for i in issues]}

    # -- fix (same engine as `agflow fix`: preview, choose, apply, undo) ------------------------
    @app.get("/api/fix/plan")
    def fix_plan() -> dict[str, Any]:
        from agflow import fixes

        hist = fixes.history()
        return {**fixes.build_plan(ws).to_dict(),
                "last_batch": {k: hist[0][k] for k in ("id", "time", "fixes", "backup_dir")} if hist else None}

    @app.post("/api/fix/apply")
    def fix_apply(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        from agflow import fixes

        selections = body.get("selections")
        if not isinstance(selections, dict) or not selections or not all(
                isinstance(k, str) and (v is None or isinstance(v, str)) for k, v in selections.items()):
            raise HTTPException(400, "selections must be a non-empty object {fix_id: option_id or null}")
        return fixes.apply(ws, selections).to_dict()

    @app.post("/api/fix/undo")
    def fix_undo() -> dict[str, Any]:
        from agflow import fixes

        try:
            return fixes.undo_last()
        except fixes.FixError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/fix/history")
    def fix_history() -> list[dict[str, Any]]:
        from agflow import fixes

        return [{k: h.get(k) for k in ("id", "time", "workspace", "fixes", "backup_dir")} for h in fixes.history()]

    # -- components ---------------------------------------------------------------------------
    @app.get("/api/items")
    def items(kind: str | None = None) -> list[dict[str, Any]]:
        inv = inventory()
        return [i.to_dict() for i in inv.items if kind is None or i.kind.value == kind]

    @app.get("/api/file")
    def read_file(path: str) -> dict[str, Any]:
        p = Path(path).resolve()
        known = {i.path.resolve() for i in inventory().items}
        if p not in known or not p.is_file():
            raise HTTPException(404, "not a known component file")
        text = p.read_text(encoding="utf-8")
        return {"path": str(p), "content": text, "chars": len(text)}

    @app.post("/api/items/{kind}/{name}/toggle")
    def toggle(kind: str, name: str, enabled: bool = Body(..., embed=True)) -> dict[str, Any]:
        try:
            k = Kind(kind)
            inv = inventory()
            where = toggles.enable(inv, k, name) if enabled else toggles.disable(inv, k, name)
        except (ValueError, toggles.ToggleError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "path": str(where)}

    @app.get("/api/gemini-md")
    def get_gemini_md() -> dict[str, Any]:
        p = paths.global_layout().gemini_md
        assert p is not None
        text = p.read_text(encoding="utf-8") if p.exists() else ""
        return {"path": str(p), "content": text, "chars": len(text), "limit": paths.GEMINI_MD_CHAR_LIMIT}

    @app.put("/api/gemini-md")
    def put_gemini_md(content: str = Body(..., embed=True)) -> dict[str, Any]:
        if len(content) > paths.GEMINI_MD_CHAR_LIMIT:
            raise HTTPException(400, f"{len(content)} chars exceeds the {paths.GEMINI_MD_CHAR_LIMIT}-char cap")
        p = paths.global_layout().gemini_md
        assert p is not None
        if p.exists():
            backup(p, paths.backups_dir() / timestamp(), paths.home())
        atomic_write_text(p, content)
        return {"ok": True, "chars": len(content)}

    # -- MCP ----------------------------------------------------------------------------------
    @app.get("/api/mcp")
    def mcp_list() -> dict[str, Any]:
        servers = []
        for i in inventory().of(Kind.MCP, enabled=None):
            cfg = i.meta.get("config", {})
            servers.append({**i.to_dict(), "missing_env": mcp_registry.missing_env(cfg, dict(os.environ)),
                            "editable": i.scope in ("global", "workspace")})
        return {"servers": servers, "catalog": mcp_registry.CATALOG}

    @app.post("/api/mcp")
    def mcp_add(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            f = mcp_registry.config_file(body.get("scope", "global"), ws)
            spec = mcp_registry.add_server(f, body["name"], command=body.get("command") or None,
                                           args=body.get("args") or [], env=body.get("env") or None,
                                           url=body.get("url") or None, replace=bool(body.get("replace")))
        except (KeyError, mcp_registry.McpError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "spec": spec}

    @app.post("/api/mcp/{scope}/{name}/toggle")
    def mcp_toggle(scope: str, name: str, enabled: bool = Body(..., embed=True)) -> dict[str, Any]:
        try:
            mcp_registry.set_enabled(mcp_registry.config_file(scope, ws), name, enabled)
        except mcp_registry.McpError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True}

    @app.delete("/api/mcp/{scope}/{name}")
    def mcp_remove(scope: str, name: str) -> dict[str, Any]:
        try:
            mcp_registry.remove_server(mcp_registry.config_file(scope, ws), name)
        except mcp_registry.McpError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True}

    # -- profiles, install, migrate --------------------------------------------------------------
    @app.get("/api/profiles")
    def profiles() -> dict[str, Any]:
        from agflow.profiles import load_profiles, resolve_profile

        out = {}
        for name, spec in load_profiles(None, ws).items():
            try:
                resolved = resolve_profile(name, None, ws)
            except Exception as exc:  # show broken profiles instead of failing the page
                resolved = {"error": str(exc)}
            out[name] = {"description": (spec or {}).get("description", ""), **resolved}
        return out

    @app.post("/api/profiles/{name}/apply")
    def profile_apply(name: str) -> dict[str, Any]:
        from agflow.profiles import ProfileError, resolve_profile

        try:
            actions = toggles.apply_profile(ws, resolve_profile(name, None, ws))
        except (ProfileError, toggles.ToggleError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "actions": actions}

    @app.post("/api/install")
    def do_install(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        from agflow.install import InstallError, install
        from agflow.profiles import ProfileError

        scope = body.get("scope", "global")
        try:
            plan = install(scope, ws, profile=body.get("profile") or None, dry_run=bool(body.get("dry_run")),
                           allow_duplicate=bool(body.get("allow_duplicate")))
        except (InstallError, ProfileError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "actions": plan.actions, "warnings": plan.warnings}

    @app.post("/api/uninstall")
    def do_uninstall(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        from agflow.install import InstallError, uninstall

        scope = body.get("scope", "global")
        try:
            plan = uninstall(scope, ws if scope == "workspace" else None, dry_run=bool(body.get("dry_run")))
        except InstallError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "actions": plan.actions}

    @app.post("/api/migrate")
    def do_migrate(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        from agflow import fixes
        from agflow.migrate import migrate_all

        # a real run is recorded in the fix history, so "Undo last fix" reverts it
        report = migrate_all(ws, dry_run=True) if body.get("dry_run", True) else fixes.migrate_undoable(ws)
        return {"ok": True, "actions": report.actions, "conflicts": report.conflicts}

    # -- graphs ---------------------------------------------------------------------------------
    @app.get("/api/graphs")
    def graphs() -> list[dict[str, Any]]:
        return [{"id": r.id, "title": r.title, "description": r.description, "source": r.source,
                 "path": str(r.path), "error": r.error} for r in catalog.list_graphs(ws)]

    @app.get("/api/graphs/{graph_id}")
    def graph_detail(graph_id: str) -> dict[str, Any]:
        try:
            g, p = catalog.resolve(graph_id, ws)
        except GraphError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"path": str(p), "yaml": p.read_text(encoding="utf-8"), "layout": graph_layout(g),
                "mermaid": to_mermaid(g), "editable": _is_workspace_graph(p)}

    @app.post("/api/graphs/validate")
    def graph_validate(yaml: str = Body(..., embed=True)) -> dict[str, Any]:
        try:
            g = parse_graph_text(yaml)
        except GraphError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "layout": graph_layout(g), "mermaid": to_mermaid(g)}

    @app.put("/api/graphs/{graph_id}")
    def graph_save(graph_id: str, yaml: str = Body(..., embed=True)) -> dict[str, Any]:
        try:
            g = parse_graph_text(yaml)
        except GraphError as exc:
            raise HTTPException(400, str(exc)) from exc
        if g.id != graph_id:
            raise HTTPException(400, f"graph id in YAML ({g.id}) must match the URL ({graph_id})")
        target = paths.workspace_layout(ws).graphs_dir / f"{g.id}.yaml"  # type: ignore[operator]
        atomic_write_text(target, yaml)
        return {"ok": True, "path": str(target)}

    def _is_workspace_graph(p: Path) -> bool:
        gd = paths.workspace_layout(ws).graphs_dir
        return gd is not None and p.parent.resolve() == gd.resolve()

    # -- runs -------------------------------------------------------------------------------------
    @app.get("/api/runs")
    def runs() -> list[dict[str, Any]]:
        return [_run_brief(r) for r in RunStore(ws).list()[:100]]

    def _run_detail(run_id: str) -> dict[str, Any]:
        store = RunStore(ws)
        try:
            state = store.load(run_id)
        except RunNotFound as exc:
            raise HTTPException(404, f"unknown run {run_id}") from exc
        g = state.graph_model()
        return {**engine.status_view(g, state, verbose=True), "layout": graph_layout(g),
                "ledger": store.read_ledger(run_id, limit=200), "updated_at": state.updated_at}

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: str) -> dict[str, Any]:
        return _run_detail(run_id)

    @app.post("/api/stream-ticket")
    def stream_ticket(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        """Exchange the (header) token for a one-time ticket to open the live stream of one run."""
        run_id = body.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            raise HTTPException(400, "run_id must be a non-empty string")
        if not RunStore(ws).exists(run_id):
            raise HTTPException(404, f"unknown run {run_id}")
        ticket = tickets.issue(run_id)
        if ticket is None:
            raise HTTPException(429, "too many stream tickets outstanding; try again in a moment")
        return {"ticket": ticket, "expires_in": tickets.ttl}

    @app.get("/api/runs/{run_id}/stream")
    async def run_stream(run_id: str, request: Request) -> StreamingResponse:
        store = RunStore(ws)
        if not store.exists(run_id):
            raise HTTPException(404, f"unknown run {run_id}")
        state_file = store.run_dir(run_id) / "state.json"

        async def events() -> Any:
            last = None
            deadline = time.time() + stream_seconds
            while time.time() < deadline and not await request.is_disconnected():
                mtime = state_file.stat().st_mtime if state_file.exists() else None
                if mtime != last:
                    last = mtime
                    yield f"data: {json.dumps(_run_detail(run_id), default=str)}\n\n"
                else:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(stream_poll)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store"})

    @app.post("/api/runs/{run_id}/approve")
    def run_approve(run_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        from agflow.runner.runner import approve

        try:
            events = approve(RunStore(ws), run_id, body["node"], approved=bool(body.get("approved", True)),
                             note=body.get("note", ""))
        except (KeyError, RunNotFound, engine.EngineError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "events": events}

    @app.post("/api/runs/{run_id}/abort")
    def run_abort(run_id: str, reason: str = Body("aborted from dashboard", embed=True)) -> dict[str, Any]:
        store = RunStore(ws)
        try:
            with store.transaction(run_id) as state:
                engine.abort(state, reason, now=time.time())
        except RunNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        store.ledger(run_id, {"event": "aborted", "reason": reason})
        return {"ok": True}

    # -- workspace ------------------------------------------------------------------------------------
    @app.get("/api/workspaces")
    def list_workspaces() -> dict[str, Any]:
        """The workspace being managed and the others agflow has seen runs in (the side pane cannot know which
        project is open, so the page offers the choice)."""
        from agflow.graph.store import known_workspaces

        known = [str(p) for p in known_workspaces()]
        if str(ws) not in known:
            known.insert(0, str(ws))
        return {"current": str(ws), "known": known, "sidecar": sidecar}

    @app.post("/api/workspace")
    def set_workspace(path: str = Body(..., embed=True)) -> dict[str, Any]:
        nonlocal ws
        chosen = Path(path).expanduser()
        if not chosen.is_absolute() or not chosen.is_dir():
            raise HTTPException(400, "give the absolute path of an existing folder")
        ws = chosen.resolve()
        return {"workspace": str(ws)}

    # -- workflows: what each needs, how its runs went, what is waiting for a decision ---------------------
    @app.get("/api/workflows")
    def workflows_list() -> list[dict[str, Any]]:
        from agflow import workflows

        return workflows.summary(ws)

    @app.get("/api/workflows/{graph_id}")
    def workflow_detail(graph_id: str) -> dict[str, Any]:
        from agflow import workflows

        data = workflows.detail(ws, graph_id)
        if data.get("error") and not data.get("nodes") and "unknown workflow" in data["error"]:
            raise HTTPException(404, data["error"])
        return data

    @app.post("/api/workflows/{graph_id}/propose")
    def workflow_propose(graph_id: str) -> dict[str, Any]:
        from agflow import improve, workflows

        try:
            made = improve.propose(ws, graph_id)
        except improve.ImproveError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"proposal": workflows.proposal_row(ws, made) if made else None}

    @app.post("/api/workflows/{graph_id}/undo")
    def workflow_undo(graph_id: str, force: bool = Body(False, embed=True)) -> dict[str, Any]:
        from agflow import improve, workflows

        try:
            return {"proposal": workflows.proposal_row(ws, improve.undo(ws, graph_id, force=force))}
        except improve.ImproveError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/proposals/{proposal_id}/apply")
    def proposal_apply(proposal_id: str, only: list[str] | None = Body(None, embed=True)) -> dict[str, Any]:
        from agflow import improve, workflows

        try:
            return {"proposal": workflows.proposal_row(ws, improve.apply(ws, proposal_id, only=only or None))}
        except improve.ImproveError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/proposals/{proposal_id}/reject")
    def proposal_reject(proposal_id: str, reason: str = Body("", embed=True)) -> dict[str, Any]:
        from agflow import improve, workflows

        try:
            return {"proposal": workflows.proposal_row(ws, improve.reject(ws, proposal_id, reason))}
        except improve.ImproveError as exc:
            raise HTTPException(400, str(exc)) from exc

    # -- AG-Flow's own health: Antigravity compatibility, updates, the panel itself -------------------------
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        """Cached and offline: what the daily check last found. The page shows it in the top bar on every screen."""
        from agflow import compat, maintenance, sidecars, update

        report = compat.load_last()
        latest = update.ReleaseInfo.from_json(update.load_state().get("latest"))
        return {**maintenance.health(), "sync": report.to_json() if report else None,
                "latest": latest.to_json() if latest else None, "sidecar_mode": sidecar,
                "sidecars": [{**s.to_json(), "steps": [] if s.ready or not (s.installed and s.valid)
                              else sidecars.enable_steps(s.id)} for s in sidecars.status()],
                "pill": sidecars.pill_link(), "update_config": update.load_config().to_json()}

    @app.post("/api/sync/check")
    def sync_check() -> dict[str, Any]:
        from agflow import compat

        return compat.run().to_json()

    @app.post("/api/sync/apply")
    def sync_apply(dry_run: bool = Body(True, embed=True)) -> dict[str, Any]:
        """Re-verify AG-Flow against the installed Antigravity (doctor and the hook probe run first) and accept it."""
        from agflow import compat

        done = compat.apply(ws, dry_run=dry_run)
        return {"report": done.report.to_json(), "problems": done.problems, "accepted": done.accepted,
                "would_accept": done.would_accept, "notes": done.notes}

    @app.post("/api/updates/check")
    def updates_check(force: bool = Body(True, embed=True)) -> dict[str, Any]:
        from agflow import update

        return update.check(force=force).to_json()

    @app.get("/api/updates/plan")
    def updates_plan() -> dict[str, Any]:
        """What updating would do. Looks at the release feed; changes nothing. Applying is a terminal command on
        purpose: replacing the package under a running server is not something to trigger from its own page."""
        from agflow import update
        from agflow.release_feed import FeedError

        try:
            return {"plan": update.plan().to_json()}
        except FeedError as exc:
            return {"plan": None, "error": str(exc)}

    # -- static UI ----------------------------------------------------------------------------------
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    return app


def _run_brief(r: engine.RunState) -> dict[str, Any]:
    return {"run_id": r.run_id, "graph_id": r.graph_id, "status": r.status, "counts": engine.counts(r),
            "created_at": r.created_at, "updated_at": r.updated_at}


class SidecarError(RuntimeError):
    """The manager was asked to run as an Antigravity sidecar but Antigravity's handshake is not there."""


def sidecar_settings(env: dict[str, str] | None = None) -> tuple[int, str]:
    """``(port, token)`` Antigravity gave this sidecar. Both are required: a manager that could change files must not
    listen without the token Antigravity hands to its pane."""
    env = os.environ if env is None else env
    port = (env.get(SIDECAR_PORT_ENV) or "").strip()
    if not port.isdigit() or not 0 < int(port) < 65536:
        raise SidecarError(f"{SIDECAR_PORT_ENV} is not set: this mode is started by Antigravity "
                           "(in a browser use `agflow dashboard` instead)")
    token = next((env[k].strip() for k in SIDECAR_TOKEN_ENVS if env.get(k, "").strip()), "")
    if not token:
        raise SidecarError("Antigravity did not give the sidecar a token (ANTIGRAVITY_SIDECAR_UI_TOKEN): refusing to "
                           "serve an unauthenticated manager")
    return int(port), token


def sidecar_workspace(env: dict[str, str] | None = None, hint: Path | None = None) -> Path:
    """Which workspace the panel starts on. Antigravity runs the sidecar from the plugin's own folder, so the project is
    not known: the explicit hint, ``AGFLOW_WORKSPACE``, then the workspace that most recently had a run, else the
    current folder's. The page lets the user switch."""
    from agflow.graph.store import known_workspaces

    env = os.environ if env is None else env
    if hint is not None:
        return hint.expanduser().resolve()
    chosen = env.get("AGFLOW_WORKSPACE", "").strip()
    if chosen and Path(chosen).expanduser().is_dir():
        return Path(chosen).expanduser().resolve()
    recent = known_workspaces(1)
    return recent[0].resolve() if recent else paths.find_workspace()


def serve_sidecar(workspace: Path | None = None) -> None:
    """Run as Antigravity's side-pane panel: 127.0.0.1 only, on the port and with the token Antigravity gave us."""
    import uvicorn

    port, token = sidecar_settings()
    ws = sidecar_workspace(hint=workspace)
    print(f"agflow manager (sidecar) for {ws} on 127.0.0.1:{port}", flush=True)
    uvicorn.run(create_app(ws, token, sidecar=True), host="127.0.0.1", port=port, log_level="warning")


def serve(workspace: Path, *, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    import uvicorn

    token = os.environ.get("AGFLOW_DASHBOARD_TOKEN") or secrets.token_urlsafe(24)
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}/#token={token}"
    print(f"agflow dashboard for {workspace}\n  {url}\n  (Ctrl+C to stop)", flush=True)
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(create_app(workspace, token), host=host, port=port, log_level="warning")
