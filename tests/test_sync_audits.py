"""Sync audits S1-S10 (PLAN.md section 7): machine checks that the parts of agflow that describe the same thing agree.

Each audit reads the repository (its source, its documents, its goldens) rather than running a feature, and fails with
the list of what disagrees. They are marked ``sync_audit``: ``pytest -m sync_audit -q`` runs just these, and the Phase 4
criterion is that two consecutive runs are clean.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

import pytest

from agflow import hooks, platform_vocab
from agflow.graph import engine
from agflow.graph.store import LEDGER_EVENTS, RunStore
from agflow.install import install, uninstall
from tests.conftest import PLUGIN, REPO
from tests.test_docs_sync import CANONICAL, DOCS, drift, live_signatures

pytestmark = pytest.mark.sync_audit

SRC_FILES = sorted((REPO / "src").rglob("*.py"))
SCRIPT_FILES = sorted((REPO / "scripts").glob("*.py"))
README = (REPO / "README.md").read_text(encoding="utf-8")


def parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def docstring_ids(tree: ast.AST) -> set[int]:
    """Identity of every docstring constant: prose, never data."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            value = first.value if isinstance(first, ast.Expr) else None
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                out.add(id(value))
    return out


def relative(path: Path) -> str:
    try:
        return path.relative_to(REPO).as_posix()
    except ValueError:  # a file the self-tests wrote outside the repository
        return path.as_posix()


# --- S1: where Antigravity's directories and files are named --------------------------------------

PATH_NAMES = {".gemini", "skills", "agents", "rules", "workflows", "plugins", "hooks.json", "mcp_config.json",
              "GEMINI.md", "global_workflows"}
PATH_BUILDERS = {"Path", "join", "joinpath", "glob", "rglob", "with_name"}
# (file, text) -> why this is not a place Antigravity looks. Empty on purpose: add an entry only with a reason.
S1_ALLOWED: dict[tuple[str, str], str] = {}


def path_literals(tree: ast.AST) -> list[ast.Constant]:
    """String constants that build a path: an operand of ``/`` or an argument of Path(), join(), glob()..."""
    found: list[ast.Constant] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            found += [side for side in (node.left, node.right)
                      if isinstance(side, ast.Constant) and isinstance(side.value, str)]
        elif isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name in PATH_BUILDERS:
                found += [a for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
    return found


def s1_offenders(files: list[Path]) -> list[str]:
    offenders = []
    for path in files:
        if path.name == "paths.py" and path.parent.name == "agflow":
            continue  # the one module that owns Antigravity's layout
        tree = parse(path)
        skip = docstring_ids(tree)
        seen: set[int] = set()
        candidates = path_literals(tree) + [n for n in ast.walk(tree) if isinstance(n, ast.Constant)
                                            and isinstance(n.value, str) and n.value.endswith(".gemini")]
        for const in candidates:
            if id(const) in seen or id(const) in skip:
                continue
            seen.add(id(const))
            text = const.value
            named = text in PATH_NAMES or text.endswith(".gemini") or text.split("/")[0] in PATH_NAMES
            if named and (relative(path), text) not in S1_ALLOWED:
                offenders.append(f"{relative(path)}:{const.lineno}: {text!r}")
    return offenders


def test_s1_antigravity_paths_are_named_only_in_paths_py():
    assert s1_offenders(SRC_FILES + SCRIPT_FILES) == [], (
        "build the path through agflow.paths (a Layout field or a helper), or allow-list it with a reason")


def test_s1_the_scan_itself_finds_what_it_claims_to(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text('"""A docstring about .gemini is prose."""\nfrom pathlib import Path\n'
                   'A = Path.home() / ".gemini" / "skills"\nB = Path("x").joinpath("rules")\n'
                   'C = "~/.gemini"\nD = "skills"  # a dict key, not a path\n', encoding="utf-8")
    found = s1_offenders([bad])
    assert sorted(f.rsplit(": ", 1)[1] for f in found) == ["'.gemini'", "'rules'", "'skills'", "'~/.gemini'"], found


# --- S2: the run schema, the ledger's vocabulary, the MCP tools -----------------------------------


def sample(annotation, name: str):
    """A non-default value for a field of RunState/NodeState, built from its type."""
    import typing

    origin, args = typing.get_origin(annotation), typing.get_args(annotation)
    if annotation is bool:
        return True
    if annotation is int:
        return 7
    if annotation is float:
        return 1234.5
    if annotation is str:
        return f"sample-{name}"
    if origin is typing.Literal:
        return args[-1]
    if isinstance(annotation, type) and issubclass(annotation, __import__("enum").Enum):
        return list(annotation)[-1]
    if origin in (typing.Union, __import__("types").UnionType):
        return sample(next(a for a in args if a is not type(None)), name)
    if origin is list:
        inner = args[0] if args else str
        return [sample(inner, name)]
    if origin is dict:
        value = args[1] if len(args) > 1 else str
        return {f"key-{name}": sample(value, name)}
    if annotation is typing.Any:
        return {"any": name}
    if hasattr(annotation, "model_fields"):
        return annotation(**{n: sample(f.annotation, n) for n, f in annotation.model_fields.items()})
    pytest.fail(f"extend sample() in tests/test_sync_audits.py: no sample for field {name}: {annotation!r}")


@pytest.mark.parametrize("model", [engine.NodeState, engine.RunState], ids=lambda m: m.__name__)
def test_s2_every_field_of_the_run_schema_survives_a_round_trip_through_the_store(model, workspace):
    values = {name: sample(field.annotation, name) for name, field in model.model_fields.items()}
    original = model(**values)
    dumped = original.model_dump()
    for name, field in model.model_fields.items():
        default = field.get_default(call_default_factory=True) if not field.is_required() else None
        assert dumped[name] != (default.model_dump() if hasattr(default, "model_dump") else default), (
            f"{model.__name__}.{name}: the sample equals the default, so the round trip would prove nothing")
    assert model.model_validate_json(original.model_dump_json()).model_dump() == dumped
    if model is engine.RunState:
        store = RunStore(workspace)
        store._write(original)
        assert store.load(original.run_id).model_dump() == dumped, "a RunState field is lost on its way through disk"


def ledger_event_names(files: list[Path] = SRC_FILES) -> dict[str, list[str]]:
    """Every constant ``"event"`` value written by a dict literal in src/, with where it is written."""
    found: dict[str, list[str]] = {}
    for path in files:
        for node in ast.walk(parse(path)):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values, strict=True):
                    if (isinstance(key, ast.Constant) and key.value == "event" and isinstance(value, ast.Constant)
                            and isinstance(value.value, str)):
                        found.setdefault(value.value, []).append(f"{relative(path)}:{node.lineno}")
    return found


def test_s2_every_ledger_event_in_the_code_is_registered_and_every_registered_one_is_used():
    used = ledger_event_names()
    unregistered = {event: used[event] for event in set(used) - set(LEDGER_EVENTS)}
    assert unregistered == {}, f"events written but not in graph.store.LEDGER_EVENTS: {unregistered}"
    assert sorted(set(LEDGER_EVENTS) - set(used)) == [], "registered events that no code writes any more"
    assert all(description.strip() for description in LEDGER_EVENTS.values())


async def test_s2_s5_the_mcp_tools_and_their_documentation_agree():
    live = await live_signatures()
    table = dict(re.findall(r"^\|\s*`(graph_[a-z]+)`\s*\|\s*`(graph_[a-z]+\([^`\n]*\))`\s*\|", CANONICAL.read_text(
        encoding="utf-8"), re.M))
    assert table == live
    problems = {relative(p): found for p in DOCS
                if (found := drift(p.read_text(encoding="utf-8"), live, allow_table=p == CANONICAL))}
    assert problems == {}, problems


# --- S3: the hook contract ------------------------------------------------------------------------

FIXTURE_DIR = REPO / "tests" / "fixtures" / "hook_payloads"


def test_s3_every_hook_payload_fixture_is_classified_as_declared_and_documented(workspace):
    readme = (FIXTURE_DIR / "README.md").read_text(encoding="utf-8")
    fixtures = sorted(FIXTURE_DIR.glob("*.json"))
    assert fixtures
    for fixture in fixtures:
        data = json.loads(fixture.read_text(encoding="utf-8").replace("<WS>", workspace.as_posix()))
        assert hooks.payload_problems(data["event"], data["payload"]) == data["expect"]["problems"], fixture.name
        assert fixture.name in readme, f"{fixture.name} is not described in the fixtures' README"


def test_s3_the_readme_names_only_payload_keys_the_vocabulary_knows():
    vocab = platform_vocab.builtin()
    known = {key for group in ("tool_call", "tool_name", "tool_args", "command", "path", "workspace", "common")
             for key in vocab.keys(group)}
    mentioned = set()
    for paragraph in re.split(r"\n\s*\n", README):
        if "payload" in paragraph.lower():
            mentioned |= set(re.findall(r"`((?:[a-z]+[A-Z][A-Za-z]*)|(?:[A-Z][a-z]+[A-Z][A-Za-z]*))`", paragraph))
    unknown = sorted(mentioned - known - {"CommandLine"})
    assert unknown == [], f"the README names payload keys that platform_vocab does not know: {unknown}"
    assert {"toolCall", "workspacePaths"} <= mentioned, "the README should describe the documented payload layout"


# --- S4: Antigravity's renameable names live in platform_vocab.py ---------------------------------


def renameable_names() -> set[str]:
    vocab = platform_vocab.builtin()
    names = set(vocab.agent_tools) | set(vocab.command_tools) | set(vocab.file_tools) | set(vocab.default_agent_tools)
    names |= set(vocab.sdk_tool_map) | set(vocab.sdk_tool_map.values()) | set(vocab.agent_models)
    names |= set(vocab.hook_events)
    return names - {"inherit"}  # "inherit" is agflow's word for "no model chosen", not one of Antigravity's identifiers


def hardcoded_names(files: list[Path]) -> list[str]:
    names = renameable_names()
    offenders = []
    for path in files:
        if path.name == "platform_vocab.py":
            continue
        tree = parse(path)
        skip = docstring_ids(tree)
        offenders += [f"{relative(path)}:{n.lineno}: {n.value!r}" for n in ast.walk(tree)
                      if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in names
                      and id(n) not in skip]
    return offenders


def test_s4_no_tool_model_or_hook_event_name_is_hard_coded_outside_platform_vocab():
    offenders = hardcoded_names(SRC_FILES + SCRIPT_FILES)
    assert offenders == [], "read the name from agflow.platform_vocab so that an override or a new version applies"


def test_s2_s4_the_scans_see_what_they_claim_to(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text('"""run_command is prose here."""\nX = {"event": "brand_new_event", "n": 1}\n'
                      'Y = {"event": variable}\nZ = "view_file"\nW = "run_command"\n', encoding="utf-8")
    assert list(ledger_event_names([sample])) == ["brand_new_event"], "only constant event names count"
    assert [o.rsplit(": ", 1)[1] for o in hardcoded_names([sample])] == ["'view_file'", "'run_command'"]


def test_s4_the_names_agflow_branches_on_are_still_in_the_vocabulary():
    """Rule triggers and execution policies are *behaviour* agflow defines (``glob`` needs ``globs``, ``off`` forbids
    commands). The code may name them, but the vocabulary must still contain every one it names."""
    vocab = platform_vocab.builtin()
    assert {"always_on", "model_decision", "glob", "manual"} <= set(vocab.rule_triggers)
    assert {"auto", "off"} <= set(vocab.exec_policies)


def test_s4_every_bundled_agent_uses_only_known_tools():
    from agflow import frontmatter

    vocab = platform_vocab.builtin()
    problems = []
    for path in sorted((PLUGIN / "agents").glob("*.md")):
        meta, _ = frontmatter.parse(path.read_text(encoding="utf-8"))
        for tool in meta.get("tools") or []:
            if tool not in vocab.agent_tools and not str(tool).startswith("mcp"):
                problems.append(f"{path.name}: {tool}")
        if meta.get("model") not in (None, *vocab.agent_models):
            problems.append(f"{path.name}: model {meta.get('model')}")
    assert problems == []


async def test_s4_tool_identifiers_in_the_master_prompt_are_real():
    vocab = platform_vocab.builtin()
    real = set(vocab.agent_tools) | set(await live_signatures())
    text = (PLUGIN / "GEMINI.md").read_text(encoding="utf-8")
    mentioned = set(re.findall(r"`([a-z]+(?:_[a-z]+)+)`", text))
    assert mentioned - real == set(), f"GEMINI.md names tools that exist nowhere: {sorted(mentioned - real)}"


# --- S6: the dashboard's browser code and its routes ----------------------------------------------

# GET /  is the static shell; /openapi.json and /static are FastAPI's own
S6_UNUSED_BY_THE_PAGE = {("GET", "/api/fix/history"): "part of the dashboard's API for scripts and other clients "
                                                       "(removing a route would break them); the page itself shows "
                                                       "the last batch from /api/fix/plan"}


def js_calls() -> list[tuple[str, str]]:
    """(method, sample path) of every ``api(...)`` call and the EventSource of the run page in app.js."""
    js = (REPO / "src" / "agflow" / "dashboard" / "static" / "app.js").read_text(encoding="utf-8")
    calls = []
    for match in re.finditer(r"\bapi\(", js):
        depth, i = 1, match.end()
        while depth and i < len(js):
            depth += {"(": 1, ")": -1}.get(js[i], 0)
            i += 1
        text = js[match.end():i - 1]
        literal = re.match(r"\s*([`\"'])(/[^`\"']*)\1(\s*\+)?", text)
        if not literal:
            continue
        path = re.sub(r"\$\{[^}]*\}", "X", literal.group(2).split("?")[0])
        if literal.group(3) and literal.group(2).endswith("/"):  # "/graphs/" + encodeURIComponent(x) is a path part...
            path += "X"                                             # ..."/items?kind=" + kind is a query value
        method = re.search(r'method:\s*"(\w+)"', text)
        calls.append((method.group(1) if method else "GET", "/api" + path))
    stream = re.search(r"EventSource\(`(/api/[^`?]*)", js)
    if stream:
        calls.append(("GET", re.sub(r"\$\{[^}]*\}", "X", stream.group(1))))
    return calls


def app_routes(workspace: Path) -> list[tuple[str, re.Pattern[str], str]]:
    from agflow.dashboard.app import create_app

    routes = []
    for route in create_app(workspace, "token").routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/"):
            continue
        pattern = re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$")
        routes += [(method, pattern, path) for method in getattr(route, "methods", ())]
    return routes


def test_s6_every_call_the_page_makes_has_a_route(workspace):
    routes = app_routes(workspace)
    calls = js_calls()
    assert len(calls) >= 25, f"the call scan found only {len(calls)} calls; the scanner is out of date"
    missing = [f"{method} {path}" for method, path in calls
               if not any(m == method and pattern.match(path) for m, pattern, _ in routes)]
    assert missing == [], f"app.js calls routes that the dashboard does not serve: {missing}"


def test_s6_every_route_is_used_by_the_page_or_allow_listed_with_a_reason(workspace):
    calls = js_calls()
    unused = [(m, p) for m, pattern, p in app_routes(workspace)
              if not any(method == m and pattern.match(path) for method, path in calls)
              and (m, p) not in S6_UNUSED_BY_THE_PAGE]
    assert unused == [], f"routes nothing calls (remove them, or allow-list with a reason): {unused}"
    assert all(reason.strip() for reason in S6_UNUSED_BY_THE_PAGE.values())


# --- S7: install and uninstall are inverses -------------------------------------------------------


BACKUPS = ".agflow-backups"  # kept on purpose after an uninstall: they are the user's record of what was overwritten


def tree_digest(*roots: Path) -> dict[str, str]:
    return {f"{root.name}/{p.relative_to(root).as_posix()}": hashlib.sha256(p.read_bytes()).hexdigest()
            for root in roots for p in sorted(root.rglob("*")) if p.is_file() and BACKUPS not in p.parts}


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf-file", "crlf-file"])
@pytest.mark.parametrize(("scope", "profile"), [("global", None), ("global", "core"), ("workspace", None),
                                                 ("workspace", "core")])
def test_s7_install_then_uninstall_leaves_the_tree_as_it_was(home, workspace, scope, profile, newline):
    (home / ".gemini").mkdir(exist_ok=True)
    (home / ".gemini" / "GEMINI.md").write_bytes(f"# my rules{newline}Be kind.{newline}".encode())
    (home / ".gemini" / "settings.json").write_text('{"theme": "dark"}', encoding="utf-8")
    (workspace / ".agents" / "rules").mkdir(parents=True)
    (workspace / ".agents" / "rules" / "mine.md").write_bytes(
        f"---{newline}trigger: manual{newline}---{newline}".encode())
    ws = workspace if scope == "workspace" else None
    before = tree_digest(home, workspace)
    install(scope, ws, profile=profile)
    assert tree_digest(home, workspace) != before, "the install changed nothing, so the round trip proves nothing"
    uninstall(scope, ws)
    after = tree_digest(home, workspace)
    left = sorted(set(after) - set(before))
    gone = sorted(set(before) - set(after))
    changed = sorted(k for k in before if k in after and before[k] != after[k])
    assert (left, gone, changed) == ([], [], []), f"uninstall left={left} removed={gone} changed={changed}"


def test_s7_fixes_uses_only_the_public_install_api():
    tree = parse(REPO / "src" / "agflow" / "fixes.py")
    private = [f"{n.value.id}.{n.attr}" for n in ast.walk(tree)
               if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in ("inst", "install")
               and n.attr.startswith("_")]
    assert private == []


# --- S8: goldens regenerate without a diff --------------------------------------------------------


def test_s8_the_report_key_snapshot_regenerates_identically(workspace, tmp_path):
    import importlib.util

    from tests.test_report_schema import SMOKE_GRAPH, SNAPSHOT, fake_run_report

    spec = importlib.util.spec_from_file_location("report_schema", REPO / "scripts" / "report_schema.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report_file = tmp_path / "report.json"
    report_file.write_text(json.dumps(fake_run_report(workspace, str(SMOKE_GRAPH))), encoding="utf-8")
    target = tmp_path / "keys.json"
    assert module.main([str(report_file), "--snapshot", str(target), "--update"]) == 0
    assert target.read_bytes() == SNAPSHOT.read_bytes()


def test_s8_the_sdk_snapshot_regenerates_identically():
    pytest.importorskip("google.antigravity")
    from tests.test_sdk_contract import SNAPSHOTS, _installed_version, _snapshot_for, describe_sdk

    snapshot = _snapshot_for(_installed_version())
    assert snapshot is not None and snapshot.parent == SNAPSHOTS
    assert (json.dumps(describe_sdk(), indent=2, sort_keys=True) + "\n").encode("utf-8") == snapshot.read_bytes()


def test_s8_the_vocabulary_golden_still_matches_the_packaged_table():
    """The packaged table is the single source: every field it lists is read back unchanged by ``builtin()``."""
    raw = json.loads((REPO / "src" / "agflow" / "data" / "platform_vocab.json").read_text(encoding="utf-8"))
    vocab = platform_vocab.builtin()
    assert vocab.version == raw["vocab_version"]
    assert set(vocab.sdk_tool_map) == set(raw["sdk_tool_map"])
    assert list(vocab.agent_tools) == list(raw["agent_tools"])


# --- S9: the README against what is measurable ----------------------------------------------------


def test_s9_the_readme_counts_match_the_bundled_plugin():
    counts = {"Skills": len(list((PLUGIN / "skills").glob("*/SKILL.md"))),
              "Subagents": len(list((PLUGIN / "agents").glob("*.md"))),
              "Graphs": len(list((PLUGIN / "graphs").glob("*.yaml")))}
    claimed = {name: int(n) for name, n in re.findall(r"\|\s*(Skills|Subagents|Graphs) \((\d+)\)", README)}
    assert claimed == counts


def test_s9_the_readme_python_requirement_matches_pyproject():
    import tomllib

    minimum = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["requires-python"]
    assert re.search(r"Python ≥ " + re.escape(minimum.removeprefix(">=")), README), (minimum, "not in the README")


def test_s9_the_readme_coverage_floor_is_the_one_ci_enforces():
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    floor = re.search(r"--cov-fail-under=(\d+)", ci)
    claim = re.search(r"CI fails below (\d+) %", README)
    assert floor and claim and floor.group(1) == claim.group(1)


def test_s9_the_readme_makes_no_unmeasured_hook_latency_claim():
    """Latency numbers are fine when they say how and where they were measured; a bare "about 70 ms" is not."""
    measured = README.replace("about 3–5 ms", "").replace("0.3–1 ms", "")
    assert not re.search(r"\b(?:about|around|~)\s*\d+\s*ms\b", measured)
    assert "scripts/bench.py" in README and "yours will differ" in README


def test_s9_the_readme_command_list_is_the_cli():
    import typer

    from agflow.cli import app

    top = set(typer.main.get_command(app).commands)
    block = README.split("## Manager CLI", 1)[1].split("```text", 1)[1].split("```", 1)[0]  # prose also starts "agflow"
    listed = {name for group in re.findall(r"^agflow ([a-z][a-z|-]*)", block, re.M) for name in group.split("|")}
    internal = {"hook": "called by Antigravity through hooks.json, not typed by a person"}
    missing = top - listed - set(internal)
    assert listed - top == set(), f"the README lists commands the CLI does not have: {sorted(listed - top)}"
    assert missing == set(), f"commands missing from the README: {sorted(missing)}"


# --- S10: every finding states its doctor rule ----------------------------------------------------


def fixlog_blocks() -> dict[str, str]:
    text = (REPO / "FIXLOG.md").read_text(encoding="utf-8")
    return {m.group(1): m.group(0) for m in re.finditer(r"^### (F\d+) .*?(?=^### |^## |\Z)", text, re.S | re.M)}


def test_s10_fixlog_has_an_entry_for_every_finding_with_a_doctor_rule_cell():
    blocks = fixlog_blocks()
    assert sorted(blocks, key=lambda f: int(f[1:])) == [f"F{n}" for n in range(1, 36)]
    empty = []
    for finding, block in blocks.items():
        cell = re.search(r"\*\*Doctor rules?:\*\*\s*(.+?)(?:\s+·\s+\*\*|\n|$)", block)
        if not cell or len(cell.group(1).strip()) < 3:
            empty.append(finding)
    assert empty == [], f"no 'Doctor rule' cell (a rule name, or none and why): {empty}"


def test_s10_no_finding_is_still_pending():
    pending = [f for f, block in fixlog_blocks().items() if re.search(r"\*\*Status:\*\*\s*pending", block)]
    assert pending == []
