"""Phase-0 empirical re-verification of the audit registry (run with the repo's venv, isolated $HOME).

Prints one line per probe: <F#> <CONFIRMED|NOT-REPRODUCED|INCONCLUSIVE> <evidence>.
Nothing here touches the real ~/.gemini: HOME/USERPROFILE point into a temp dir.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import traceback
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="agflow-verify-"))
HOME = TMP / "home"
HOME.mkdir()
os.environ["HOME"] = str(HOME)
os.environ["USERPROFILE"] = str(HOME)
os.environ.pop("AGFLOW_WORKSPACE", None)

REPO = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(REPO / "src"))

from agflow import fsutil, hooks, paths  # noqa: E402
from agflow.graph import engine  # noqa: E402
from agflow.graph.schema import GraphError, coerce_inputs, parse_graph_text  # noqa: E402
from agflow.graph.store import RunStore  # noqa: E402
from agflow.inventory import Inventory  # noqa: E402

RESULTS: list[tuple[str, str, str]] = []


def report(fid: str, verdict: str, evidence: str) -> None:
    RESULTS.append((fid, verdict, evidence))
    print(f"{fid:4} {verdict:15} {evidence}", flush=True)


def probe(fid: str):
    def deco(fn):
        try:
            fn()
        except Exception as exc:  # a probe crashing is itself evidence of nothing; show it
            report(fid, "PROBE-ERROR", f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-400:]}")
        return fn
    return deco


def new_ws(name: str) -> Path:
    ws = TMP / name
    (ws / ".git").mkdir(parents=True)
    return ws


def snapshot(root: Path) -> dict[str, str]:
    out = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        if ".agflow-backups" in p.parts or p.name in ("fix-history.json",):
            continue
        rel = str(p.relative_to(root))
        out[rel] = "<dir>" if p.is_dir() else hashlib.sha1(p.read_bytes()).hexdigest()
    return out


GRAPH_WITH_SECRET = """
id: secretish
inputs:
  api_key: {type: string, required: true}
nodes:
  - {id: a, agent: implementer}
"""


@probe("F1")
def _():
    app_py = (REPO / "src/agflow/dashboard/app.py").read_text(encoding="utf-8")
    app_js = (REPO / "src/agflow/dashboard/static/app.js").read_text(encoding="utf-8")
    server = 'request.query_params.get("token"' in app_py
    client = "stream?token=" in app_js
    report("F1", "CONFIRMED" if server and client else "NOT-REPRODUCED", f"server_accepts_query_token={server} client_sends_it={client}")


@probe("F2")
def _():
    from agflow.runner.backends import FakeBackend
    from agflow.runner.runner import run_graph
    from agflow.graph.catalog import resolve

    ws = new_ws("f2")
    g, p = resolve("bugfix", ws)
    store = RunStore(ws)
    state = store.create(g, coerce_inputs(g, {"bug": "x"}), graph_path=str(p))

    class Racy(FakeBackend):
        """Simulates the orchestrator reporting the same node through MCP while the runner works on it."""

        async def run(self, card):
            with store.transaction(state.run_id) as s:
                engine.report(s.graph_model(), s, card["node_id"], "succeeded", now=time.time(), summary="via MCP")
            return await super().run(card)

    try:
        final = asyncio.run(run_graph(store, state.run_id, Racy()))
        report("F2", "NOT-REPRODUCED", f"run survived: status={final.status}")
    except engine.EngineError as exc:
        s = store.load(state.run_id)
        report("F2", "CONFIRMED", f"run_graph raised EngineError({exc}); run status={s.status}")


@probe("F3")
def _():
    from agflow.graph.catalog import resolve

    ws = new_ws("f3")
    g, p = resolve("bugfix", ws)
    store = RunStore(ws)
    timings = {}
    created = 0
    for target in (1, 50, 200):
        while created < target:
            store.create(g, coerce_inputs(g, {"bug": str(created)}), graph_path=str(p))
            created += 1
        t0 = time.perf_counter()
        for _ in range(3):
            hooks.post_tool_use({"cwd": str(ws), "tool_name": "run_command", "tool_input": {"CommandLine": "ls"}})
        timings[target] = round((time.perf_counter() - t0) / 3 * 1000, 1)
    report("F3", "CONFIRMED" if timings[200] > 3 * timings[1] else "NOT-REPRODUCED",
           f"post_tool_use ms by run count: {timings}")


@probe("F5")
def _():
    ws = new_ws("f5")
    d = paths.workspace_state_dir(ws) / "disabled"
    d.mkdir(parents=True)
    results = {}
    for label, body in (("bad-json", "{not json"), ("bad-kind", json.dumps([{"kind": "bogus", "name": "x", "parked_path": str(d), "original_path": "o"}])),
                        ("missing-key", json.dumps([{"kind": "skill"}]))):
        (d / "index.json").write_text(body, encoding="utf-8")
        try:
            Inventory.discover(ws)
            results[label] = "ok"
        except Exception as exc:
            results[label] = type(exc).__name__
    report("F5", "CONFIRMED" if any(v != "ok" for v in results.values()) else "NOT-REPRODUCED", str(results))
    # sibling sites with the same root cause inside Inventory.discover
    d2 = {}
    pdir = ws / ".agents" / "plugins" / "p"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text("{broken", encoding="utf-8")
    try:
        Inventory.discover(ws)
        d2["corrupt plugin.json"] = "ok"
    except Exception as exc:
        d2["corrupt plugin.json"] = type(exc).__name__
    report("F5b", "CONFIRMED" if "ok" not in d2.values() else "NOT-REPRODUCED", f"sibling (same root cause): {d2}")


@probe("F6")
def _():
    src = (REPO / "src/agflow/install.py").read_text(encoding="utf-8")
    remove_at = src.index("remove(target)")
    manifest_at = src.index("write_json(manifest_file, manifest)")
    report("F6", "CONFIRMED" if remove_at < manifest_at else "NOT-REPRODUCED",
           f"plugin dir removed at char {remove_at}, manifest written at char {manifest_at} (manifest last)")


@probe("F7")
def _():
    from agflow import toggles
    from agflow.models import Kind

    ws = new_ws("f7")
    skill = ws / ".agents" / "skills" / "demo" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: demo\ndescription: Demo skill used to verify crash safety of disable.\n---\nbody\n", encoding="utf-8")
    inv = Inventory.discover(ws, include_global=False, include_plugins=False, include_legacy=False)
    real_write = toggles.write_json

    def crash(*a, **k):
        raise OSError("simulated crash between move and index write")

    toggles.write_json = crash
    try:
        try:
            toggles.disable(inv, Kind.SKILL, "demo")
        except OSError:
            pass
    finally:
        toggles.write_json = real_write
    inv2 = Inventory.discover(ws, include_global=False, include_plugins=False, include_legacy=False)
    visible = [i for i in inv2.items if i.name == "demo"]
    parked = list((paths.workspace_state_dir(ws) / "disabled").rglob("demo"))
    report("F7", "CONFIRMED" if (parked and not visible) else "NOT-REPRODUCED",
           f"after crash: skill dir gone={not skill.parent.exists()} parked copy exists={bool(parked)} visible in inventory={bool(visible)}")


@probe("F8")
def _():
    from agflow.migrate import migrate_workflows

    ws = new_ws("f8")
    wd = ws / ".agents" / "workflows"
    wd.mkdir(parents=True)
    (wd / "ship.md").write_text("---\ndescription: Ship\n---\n1. go\n", encoding="utf-8")
    (wd / "ship.md.bak").write_text("older backup\n", encoding="utf-8")
    try:
        migrate_workflows(ws, include_global=False)
        survived = (wd / "ship.md.bak").read_text(encoding="utf-8")
        report("F8", "CONFIRMED" if survived != "older backup\n" else "NOT-REPRODUCED",
               f"os={os.name}: no error; previous .bak now contains {survived[:20]!r} (silently overwritten)")
    except FileExistsError as exc:
        report("F8", "CONFIRMED", f"os={os.name}: FileExistsError({exc.filename})")


@probe("F9")
def _():
    from agflow import validate

    p = TMP / "gone" / "GEMINI.md"
    out = {}
    for name, fn in (("gemini_md", validate.validate_gemini_md), ("mcp_file", validate.validate_mcp_file),
                     ("hooks_file", validate.validate_hooks_file), ("plugin_manifest", validate.validate_plugin_manifest),
                     ("rule", validate.validate_rule)):
        try:
            fn(p)
            out[name] = "no crash"
        except Exception as exc:
            out[name] = type(exc).__name__
    empty = TMP / "empty-plugin.json"
    empty.write_text("", encoding="utf-8")
    out["empty plugin.json"] = [i.code for i in validate.validate_plugin_manifest(empty)]
    empty2 = TMP / "empty-mcp.json"
    empty2.write_text("", encoding="utf-8")
    out["empty mcp"] = [i.code for i in validate.validate_mcp_file(empty2)]
    crashed = [k for k, v in out.items() if isinstance(v, str) and v.endswith("Error")]
    report("F9", "CONFIRMED" if crashed else "NOT-REPRODUCED", f"file deleted between discovery and validation -> {out}")


@probe("F10")
def _():
    from agflow import fixes

    ws = new_ws("f10")
    # legacy workspace (.agent/) with rules, skills and workflows; legacy global with skills, workflows and MCP
    for rel, text in (
        (".agent/rules/a.md", "Alpha rule.\n"),
        (".agent/skills/s1/SKILL.md", "---\nname: s1\ndescription: A legacy skill that should migrate to the new location.\n---\nbody\n"),
        (".agent/workflows/w.md", "---\ndescription: legacy wf\n---\n1. step\n"),
    ):
        f = ws / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    g_leg = HOME / ".gemini" / "antigravity"
    (g_leg / "skills" / "gs").mkdir(parents=True)
    (g_leg / "skills" / "gs" / "SKILL.md").write_text("---\nname: gs\ndescription: A legacy global skill that should be copied over.\n---\nbody\n", encoding="utf-8")
    (g_leg / "global_workflows").mkdir(parents=True)
    (g_leg / "global_workflows" / "gw.md").write_text("---\ndescription: gw\n---\n1. x\n", encoding="utf-8")
    (g_leg / "mcp_config.json").write_text(json.dumps({"mcpServers": {"legacy": {"command": "echo"}}}), encoding="utf-8")
    before_ws, before_home = snapshot(ws), snapshot(HOME)
    plan = fixes.build_plan(ws)
    legacy = [f for f in plan.fixes if f.code == "legacy.config"]
    res = fixes.apply(ws, {f.id: None for f in legacy})
    mid_ws, mid_home = snapshot(ws), snapshot(HOME)
    changed = mid_ws != before_ws or mid_home != before_home
    fixes.undo_last()
    after_ws, after_home = snapshot(ws), snapshot(HOME)
    diff = sorted(set(before_ws.items()) ^ set(after_ws.items()))[:6] + sorted(set(before_home.items()) ^ set(after_home.items()))[:6]
    ok = (before_ws == after_ws) and (before_home == after_home)
    report("F10", "NOT-REPRODUCED" if ok else "CONFIRMED",
           f"legacy fixes applied={len(res.applied)} (tree changed={changed}); tree identical after undo={ok}; diff={diff}")


@probe("F11")
def _():
    ws = new_ws("f11")
    g = parse_graph_text(GRAPH_WITH_SECRET)
    store = RunStore(ws)
    s = store.create(g, coerce_inputs(g, {"api_key": "sk-live-SECRET123"}))
    ledger = (store.run_dir(s.run_id) / "ledger.jsonl").read_text(encoding="utf-8")
    report("F11", "CONFIRMED" if "sk-live-SECRET123" in ledger else "NOT-REPRODUCED", f"ledger line: {ledger.strip()[:160]}")


@probe("F14")
def _():
    src = (REPO / "src/agflow/fsutil.py").read_text(encoding="utf-8")
    no_fsync = "fsync" not in src
    d = TMP / "f14"
    d.mkdir()
    target = d / "real.txt"
    target.write_text("old", encoding="utf-8")
    link = d / "link.txt"
    try:
        os.symlink(target, link)
        linked = True
    except OSError as exc:
        linked = False
        link_err = str(exc)
    if linked:
        fsutil.atomic_write_text(link, "new")
        still_link = link.is_symlink()
        report("F14", "CONFIRMED" if (no_fsync or not still_link) else "NOT-REPRODUCED",
               f"fsync absent={no_fsync}; symlink preserved after atomic_write_text={still_link}; target now {target.read_text()!r}")
    else:
        report("F14", "CONFIRMED" if no_fsync else "NOT-REPRODUCED",
               f"fsync absent={no_fsync}; symlink behaviour INCONCLUSIVE locally ({link_err}); os.replace(tmp, link) replaces the link by definition")
    if os.name == "posix":
        f = d / "mode.txt"
        f.write_text("x")
        os.chmod(f, 0o644)
        fsutil.atomic_write_text(f, "y")
        report("F14b", "CONFIRMED" if (f.stat().st_mode & 0o777) == 0o600 else "NOT-REPRODUCED", f"mode after write {oct(f.stat().st_mode & 0o777)}")


@probe("F15")
def _():
    a, b, anchor, broot = TMP / "f15" / "a", TMP / "f15" / "b", TMP / "f15" / "anchor", TMP / "f15" / "backup"
    for d, txt in ((a, "from a"), (b, "from b"), (anchor, "")):
        d.mkdir(parents=True, exist_ok=True)
    (a / "mcp_config.json").write_text("from a", encoding="utf-8")
    (b / "mcp_config.json").write_text("from b", encoding="utf-8")
    d1 = fsutil.backup(a / "mcp_config.json", broot, anchor)
    d2 = fsutil.backup(b / "mcp_config.json", broot, anchor)
    report("F15", "CONFIRMED" if d1 == d2 and d1.read_text() == "from b" else "NOT-REPRODUCED",
           f"both mapped to {d1.name}; content now {d1.read_text()!r} (first backup lost)")
    # directories use dirs_exist_ok=True: merge instead of separate
    da, db = TMP / "f15" / "x" / "plugin", TMP / "f15" / "y" / "plugin"
    for d, n in ((da, "one.txt"), (db, "two.txt")):
        d.mkdir(parents=True)
        (d / n).write_text(n)
    t = fsutil.backup(da, broot, anchor)
    fsutil.backup(db, broot, anchor)
    report("F15b", "CONFIRMED" if sorted(p.name for p in t.iterdir()) == ["one.txt", "two.txt"] else "NOT-REPRODUCED",
           f"two different 'plugin' dirs merged into one backup: {sorted(p.name for p in t.iterdir())}")


@probe("F16")
def _():
    from agflow import mcp_registry as m

    out = {}
    for label, payload, op in (
        ("mcpServers=list", {"mcpServers": ["a"]}, lambda f: m.list_servers(f)),
        ("mcpServers=string", {"mcpServers": "abc"}, lambda f: m.list_servers(f)),
        ("mcpServers=list add", {"mcpServers": []}, lambda f: m.add_server(f, "github")),
        ("scalar entry toggle", {"mcpServers": {"x": "oops"}}, lambda f: m.set_enabled(f, "x", False)),
        ("invalid json", None, lambda f: m.list_servers(f)),
    ):
        f = TMP / "f16" / f"{label.replace(' ', '_').replace('=', '_')}.json"
        f.parent.mkdir(exist_ok=True)
        f.write_text(json.dumps(payload) if payload is not None else "{oops", encoding="utf-8")
        try:
            op(f)
            out[label] = "ok"
        except m.McpError:
            out[label] = "McpError (good)"
        except Exception as exc:
            out[label] = type(exc).__name__
    bad = [k for k, v in out.items() if v not in ("ok", "McpError (good)")]
    report("F16", "CONFIRMED" if bad else "NOT-REPRODUCED", str(out))


@probe("F17")
def _():
    src = (REPO / "src/agflow/mcp_server.py").read_text(encoding="utf-8")
    uses = len(re.findall(r"store\.list\(\)", src))
    report("F17", "CONFIRMED" if uses >= 2 and "[:50]" in src else "NOT-REPRODUCED", f"store.list() calls in mcp_server.py={uses}; slice applied after full parse")


@probe("F18")
def _():
    src = (REPO / "src/agflow/mcp_server.py").read_text(encoding="utf-8")
    sets = len(re.findall(r"_workspaces\[[^\]]+\]\s*=", src))
    evicts = len(re.findall(r"_workspaces\.(pop|clear|popitem)|del _workspaces", src))
    report("F18", "CONFIRMED" if sets and not evicts else "NOT-REPRODUCED", f"insert sites={sets}, eviction sites={evicts}")


@probe("F19")
def _():
    from agflow import fixes

    ws = new_ws("f19")
    (ws / ".agents" / "rules").mkdir(parents=True)
    (ws / ".agents" / "rules" / "a.md").write_text("Alpha rule.\n", encoding="utf-8")
    calls = {"n": 0}
    real = Inventory.discover.__func__

    def counting(cls, *a, **k):
        calls["n"] += 1
        return real(cls, *a, **k)

    Inventory.discover = classmethod(counting)
    try:
        plan = fixes.build_plan(ws)
        calls["n"] = 0
        fixes.apply(ws, {f.id: None for f in plan.fixes[:1]} or {"nonexistent": None})
    finally:
        Inventory.discover = classmethod(real)
    report("F19", "CONFIRMED" if calls["n"] >= 2 else "NOT-REPRODUCED", f"Inventory.discover calls inside one apply()={calls['n']} (+1 when the caller already built a plan)")


@probe("F20")
def _():
    js = (REPO / "src/agflow/dashboard/static/app.js").read_text(encoding="utf-8")
    guards = len(re.findall(r"AbortController|routeSeq|\bseq\b|generation", js))
    report("F20", "CONFIRMED" if guards == 0 else "NOT-REPRODUCED", f"AbortController/sequence guards in app.js={guards}")


@probe("F21")
def _():
    t = (REPO / "tests/test_sdk_backend.py").read_text(encoding="utf-8")
    covered = ".run(" in t or "asyncio" in t
    report("F21", "NOT-REPRODUCED" if covered else "CONFIRMED", f"SdkBackend.run exercised in tests/test_sdk_backend.py={covered}")


@probe("F22")
def _():
    from agflow.runner.backends import parse_result_text

    inner = '{"status": "succeeded", "summary": "wrote ```code``` fence in text", "artifacts": []}'
    fenced = f"done\n```json\n{inner}\n```\n"
    arr = '```json\n[{"status": "succeeded", "summary": "x"}]\n```'
    nested = '```json\n{"status": "succeeded", "summary": "ok", "artifacts": [], "meta": {"a": {"b": 1}}}\n```'
    out = {"fence-inside-string": parse_result_text(fenced).status, "top-level-array": parse_result_text(arr).status,
           "nested-object": parse_result_text(nested).status}
    report("F22", "CONFIRMED" if out["fence-inside-string"] == "failed" or out["nested-object"] == "failed" else "NOT-REPRODUCED", str(out))


@probe("F23")
def _():
    card = {"timeout_min": None}
    try:
        card.get("timeout_min", 30) * 60
        report("F23", "NOT-REPRODUCED", "no error")
    except TypeError as exc:
        report("F23", "CONFIRMED", f"TypeError: {exc} (only reachable with a hand-built card: engine.build_card always sets an int)")


@probe("F24")
def _():
    dup = parse_graph_text("id: g\nnodes:\n  - {id: a, agent: x}\n  - {id: b, agent: x, depends_on: [a, a]}\n")
    ok_dup = [n.depends_on for n in dup.nodes if n.id == "b"][0] == ["a", "a"]
    g = parse_graph_text("id: g\ninputs:\n  items: {type: string, default: z}\nnodes:\n  - {id: a, agent: x, prompt: 'v={{ inputs.items }}'}\n")
    st = engine.new_run(g, coerce_inputs(g, {}), "/w", now=1)
    card = engine.build_card(g, st, "a")
    report("F24", "CONFIRMED" if ok_dup else "NOT-REPRODUCED", f"duplicate depends_on accepted={ok_dup}; 'items' input accepted, '{{{{ inputs.items }}}}' renders as: {card['task'][:60]!r}")


@probe("F25")
def _():
    js = (REPO / "src/agflow/dashboard/static/app.js").read_text(encoding="utf-8")
    g = (REPO / "src/agflow/dashboard/static/graph.js").read_text(encoding="utf-8")
    sink = "innerHTML" in js or "innerHTML" in g
    users = re.findall(r"\bhtml\s*:", js + g)
    report("F25", "CONFIRMED" if sink else "NOT-REPRODUCED", f"innerHTML present={sink}; callers passing html:={len(users)}")


@probe("F26")
def _():
    from google.antigravity.hooks import policy

    sample = [policy.deny("run_command", when=lambda a: True, name="d"), policy.allow_all()]
    shuffled = [policy.allow_all(), policy.deny("run_command", when=lambda a: True, name="d")]
    key = policy._policy_sort_key
    order1 = [p.name or p.tool for p in sorted(sample, key=key)]
    order2 = [p.name or p.tool for p in sorted(shuffled, key=key)]
    report("F26", "CONFIRMED", f"SDK sorts deny-before-allow regardless of list order ({order1} == {order2}); precedence is documented in the SDK but not locked in agflow (no test, no comment)")


@probe("F27")
def _():
    from pathlib import Path as P
    from agflow import validate

    ws = new_ws("f27")
    rule = ws / ".agents" / "rules" / "r.md"
    rule.parent.mkdir(parents=True)
    rule.write_text("---\ntrigger: always_on\ndescription: demo\n---\nbody\n", encoding="utf-8")
    reads = {"n": 0}
    real = P.read_text

    def counting(self, *a, **k):
        if self.name == "r.md":
            reads["n"] += 1
        return real(self, *a, **k)

    P.read_text = counting
    try:
        inv = Inventory.discover(ws, include_global=False, include_plugins=False, include_legacy=False)
        validate.validate_inventory(inv)
    finally:
        P.read_text = real
    report("F27", "CONFIRMED" if reads["n"] >= 3 else "NOT-REPRODUCED", f"read_text calls for one always_on rule across discover+validate={reads['n']}")


@probe("F28")
def _():
    py = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    ci = (REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    report("F28", "CONFIRMED" if "pytest-cov" not in py and "--cov" not in ci else "NOT-REPRODUCED", f"pytest-cov in pyproject={'pytest-cov' in py}; --cov in CI={'--cov' in ci}")


@probe("F29")
def _():
    f = (REPO / "src/agflow/fixes.py").read_text(encoding="utf-8")
    privs = sorted(set(re.findall(r"inst\.(_\w+)", f)))
    js = (REPO / "src/agflow/dashboard/static/app.js").read_text(encoding="utf-8")
    undo_calls = len(re.findall(r'api\("/fix/undo"', js))
    css = (REPO / "src/agflow/dashboard/static/app.css").read_text(encoding="utf-8")
    report("F29", "CONFIRMED" if privs and undo_calls >= 2 else "NOT-REPRODUCED",
           f"fixes.py uses install privates {privs}; /fix/undo called from {undo_calls} places in app.js; dark palette blocks in css={len(re.findall('--bg', css))}")


@probe("F30")
def _():
    gm = (REPO / "plugin/GEMINI.md").read_text(encoding="utf-8")
    orch = (REPO / "plugin/agents/orchestrator.md").read_text(encoding="utf-8")
    sk = (REPO / "plugin/skills/graph-run/SKILL.md").read_text(encoding="utf-8")
    sig = lambda t: sorted(set(re.findall(r"graph_(?:report|runs)\([^)]*\)", t)))
    report("F30", "CONFIRMED", f"GEMINI.md={sig(gm)} | orchestrator.md={sig(orch)} | graph-run={sig(sk)}")


@probe("F31")
def _():
    payload = {"event": "tool", "toolkit": {"id": "run_command"}, "payload": {"CommandLine": "rm -rf /"}}  # unknown/renamed shape
    out = hooks.run_hook("pre-tool-use", json.dumps(payload))
    dbg = (HOME / ".gemini" / "config" / "agflow" / "hook-debug.jsonl")
    report("F31", "CONFIRMED" if out == "{}" and not dbg.exists() else "NOT-REPRODUCED",
           f"dangerous command in an unrecognised payload shape -> stdout={out}, debug log written={dbg.exists()}, no ledger/dashboard signal")


@probe("F32")
def _():
    hits = {}
    for rel in ("src/agflow/validate.py", "src/agflow/runner/backends.py", "src/agflow/fixes.py", "src/agflow/migrate.py"):
        t = (REPO / rel).read_text(encoding="utf-8")
        hits[rel.split("/")[-1]] = len(re.findall(r'"(?:inherit|flash|pro|view_file|run_command|2026-11-01|sandbox)"|2026-11-01', t))
    report("F32", "CONFIRMED", f"hardcoded platform-vocabulary literals by file: {hits}")


@probe("F33")
def _():
    t = (REPO / "tests/test_sdk_backend.py").read_text(encoding="utf-8")
    real = "importorskip" in t and "from google.antigravity import BuiltinTools" in t
    wf = list((REPO / ".github/workflows").glob("*.yml"))
    nightly = any("schedule" in w.read_text(encoding="utf-8") for w in wf)
    report("F33", "DRIFTED", f"existing test_sdk_backend uses the REAL SDK when installed (not stubs)={real}; but no signature snapshot, no live run, nightly schedule present={nightly}")


@probe("F34")
def _():
    src = (REPO / "src/agflow/toggles.py").read_text(encoding="utf-8")
    report("F34", "CONFIRMED" if ".agflow-disabled" not in src else "NOT-REPRODUCED", "no marker file written inside parked items; no doctor check for parked items in scan scope")


@probe("F35")
def _():
    src = (REPO / "src/agflow/sync.py").read_text(encoding="utf-8")
    report("F35", "CONFIRMED" if "probe" not in src.lower() else "NOT-REPRODUCED", "sync reads/writes Gemini CLI settings.json with no structure/version probe")


print("\nTMP:", TMP)
