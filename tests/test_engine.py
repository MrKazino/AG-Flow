import pytest

from agflow.graph import engine
from agflow.graph.engine import Status
from agflow.graph.schema import GraphError, coerce_inputs, parse_graph_text

PIPE = """
id: pipe
inputs:
  goal: {type: string, required: true}
  ui: {type: boolean, default: false}
defaults: {retries: 1, max_parallel: 2, timeout_min: 10}
nodes:
  - {id: spec, agent: architect, prompt: "Spec {{ inputs.goal }}", outputs: ["docs/{{ run.id }}.md"]}
  - {id: a, agent: implementer, depends_on: [spec], prompt: "Use {{ nodes.spec.outputs[0] }}"}
  - {id: b, agent: tester, depends_on: [spec]}
  - {id: c, agent: researcher, depends_on: [spec]}
  - {id: review, agent: reviewer, depends_on: [a, b, c]}
  - {id: gate, type: gate, depends_on: [review], on_fail: {goto: a, max_loops: 1}}
  - {id: ui, agent: ui-verifier, depends_on: [gate], when: inputs.ui}
  - {id: ok, type: human, prompt: "Ship?", depends_on: [ui]}
"""


def graph():
    return parse_graph_text(PIPE)


def run(inputs=None):
    g = graph()
    return g, engine.new_run(g, coerce_inputs(g, inputs or {"goal": "x"}), "/ws", now=0, run_id="r1")


def st(state):
    return {k: v.status.value for k, v in state.nodes.items()}


def ok(g, s, nid, t=1.0, **kw):
    return engine.report(g, s, nid, "succeeded", now=t, **kw)


def test_parallel_claims_respect_max_parallel():
    g, s = run()
    assert engine.claim(g, s, now=1) == ["spec"]
    ok(g, s, "spec")
    assert engine.claim(g, s, now=2) == ["a", "b"]  # max_parallel = 2
    assert engine.claim(g, s, now=2) == []
    ok(g, s, "a")
    assert engine.claim(g, s, now=3) == ["c"]


def test_prompt_rendering_and_card():
    g, s = run({"goal": "login"})
    engine.claim(g, s, now=1)
    card = engine.build_card(g, s, "spec")
    assert card["task"] == "Spec login"
    assert card["expected_outputs"] == ["docs/r1.md"]
    assert '"status": "succeeded | failed | rejected"' in card["subagent_prompt"]
    ok(g, s, "spec", summary="wrote spec")
    engine.claim(g, s, now=2)
    card_a = engine.build_card(g, s, "a")
    assert card_a["task"] == "Use docs/r1.md"
    assert card_a["upstream"][0]["summary"] == "wrote spec"


def test_failed_is_retried_then_blocks_downstream():
    g, s = run()
    engine.claim(g, s, now=1)
    ok(g, s, "spec")
    engine.claim(g, s, now=2)
    engine.report(g, s, "a", "failed", now=3, summary="boom")
    assert s.nodes["a"].status == Status.PENDING
    assert "boom" in s.nodes["a"].feedback[0]
    assert "a" in engine.claim(g, s, now=4)
    assert s.nodes["a"].attempts == 2
    engine.report(g, s, "a", "failed", now=5, summary="boom again")
    assert s.nodes["a"].status == Status.FAILED
    for nid in ("b", "c"):
        engine.claim(g, s, now=6)
        ok(g, s, nid)
    assert s.nodes["review"].status == Status.BLOCKED
    assert s.nodes["gate"].status == Status.FAILED
    assert s.status == "failed"


def test_rejected_triggers_bounded_rework():
    g, s = run()
    engine.claim(g, s, now=1)
    ok(g, s, "spec")
    assert engine.claim(g, s, now=2) == ["a", "b"]
    ok(g, s, "a")
    ok(g, s, "b")
    assert engine.claim(g, s, now=3) == ["c"]
    ok(g, s, "c")
    assert engine.claim(g, s, now=3) == ["review"]
    engine.report(g, s, "review", "rejected", now=4, summary="missing validation")
    assert s.loops == {"gate": 1}
    assert st(s)["a"] == "pending" and st(s)["review"] == "pending" and st(s)["b"] == "succeeded"
    assert any("missing validation" in f for f in s.nodes["a"].feedback)
    assert s.nodes["review"].history  # previous attempt kept for context
    assert engine.claim(g, s, now=5) == ["a"]
    ok(g, s, "a")
    assert engine.claim(g, s, now=6) == ["review"]
    engine.report(g, s, "review", "rejected", now=7, summary="still missing")
    assert s.nodes["gate"].status == Status.FAILED  # max_loops = 1 exhausted
    assert s.status == "failed"


def run_to_human(inputs):
    g, s = run(inputs)
    for _ in range(10):
        for nid in engine.claim(g, s, now=1):
            if g.node(nid).type == "agent":
                ok(g, s, nid)
    return g, s


def test_when_false_skips_and_downstream_runs():
    g, s = run_to_human({"goal": "x"})
    assert s.nodes["ui"].status == Status.SKIPPED
    assert s.nodes["ok"].status == Status.RUNNING
    view = engine.status_view(g, s)
    assert view["waiting_on_human"] == ["ok"]
    ok(g, s, "ok")
    assert s.status == "succeeded"


def test_when_true_runs_node():
    g, s = run_to_human({"goal": "x", "ui": "true"})
    assert s.nodes["ui"].status == Status.SUCCEEDED


def test_human_rejection_fails_run():
    g, s = run_to_human({"goal": "x"})
    engine.report(g, s, "ok", "rejected", now=2, summary="no")
    assert s.status == "failed"


def test_lease_expiry_counts_as_failed_attempt():
    g, s = run()
    engine.claim(g, s, now=0)
    assert s.nodes["spec"].lease_expires_at == 600
    engine.claim(g, s, now=601)  # recover_stale runs inside claim and re-claims
    assert s.nodes["spec"].attempts == 2
    assert s.nodes["spec"].status == Status.RUNNING
    engine.claim(g, s, now=1300)
    assert s.nodes["spec"].status == Status.FAILED
    assert "lease expired" in s.nodes["spec"].error


def test_release_does_not_burn_attempts():
    g, s = run()
    engine.claim(g, s, now=1)
    assert engine.release(g, s, now=2) == ["spec"]
    assert s.nodes["spec"].attempts == 0 and s.nodes["spec"].status == Status.PENDING


def test_report_errors():
    g, s = run()
    with pytest.raises(engine.EngineError):
        engine.report(g, s, "spec", "succeeded", now=1)  # not claimed
    with pytest.raises(engine.EngineError):
        engine.report(g, s, "nope", "succeeded", now=1)
    engine.claim(g, s, now=1)
    with pytest.raises(engine.EngineError):
        engine.report(g, s, "spec", "maybe", now=1)  # type: ignore[arg-type]


def test_abort():
    g, s = run()
    engine.claim(g, s, now=1)
    engine.abort(s, "user stop", now=2)
    assert s.status == "aborted"
    assert engine.claim(g, s, now=3) == []
    with pytest.raises(engine.EngineError):
        engine.report(g, s, "spec", "succeeded", now=3)


@pytest.mark.parametrize("bad, msg", [
    ("id: x\nnodes:\n  - {id: a, agent: q, depends_on: [b]}\n  - {id: b, agent: q, depends_on: [a]}", "cycle"),
    ("id: x\nnodes:\n  - {id: a, agent: q}\n  - {id: a, agent: q}", "duplicate"),
    ("id: x\nnodes:\n  - {id: a, agent: q}\n"
     "  - {id: g, type: gate, depends_on: [a], on_fail: {goto: z}}", "not a node"),
    ("id: x\nnodes:\n  - {id: a, agent: q}\n  - {id: b, agent: q}\n"
     "  - {id: g, type: gate, depends_on: [a], on_fail: {goto: b}}", "upstream"),
    ("id: x\nnodes:\n  - {id: a, type: agent}", "needs an 'agent'"),
    ("id: x\nnodes:\n  - {id: a, agent: q, prompt: '{{ unclosed'}", "template"),
    ("id: X!\nnodes:\n  - {id: a, agent: q}", "graph id"),
    ("id: x\nnodes:\n  - {id: a, agent: q, bogus: 1}", "Extra inputs"),
])
def test_schema_rejects(bad, msg):
    with pytest.raises(GraphError, match=msg):
        parse_graph_text(bad)


# --- F24a: duplicate dependencies, and an input named like a dict method --------------------------------------------


def test_a_repeated_dependency_is_rejected_and_names_the_node_and_the_dependency():
    bad = "id: x\nnodes:\n  - {id: a, agent: q}\n  - {id: b, agent: q, depends_on: [a, a]}"
    with pytest.raises(GraphError, match=r"node 'b' lists the same dependency more than once: \['a'\]"):
        parse_graph_text(bad)
    parse_graph_text("id: x\nnodes:\n  - {id: a, agent: q}\n  - {id: b, agent: q, depends_on: [a]}")  # control


def test_the_bundled_graphs_have_no_repeated_dependencies():
    from agflow.graph.schema import load_graph
    from tests.conftest import REPO

    graphs = sorted((REPO / "plugin" / "graphs").glob("*.yaml"))
    assert graphs
    for path in graphs:
        load_graph(path)  # raises on any violation


@pytest.mark.parametrize("name", ["items", "keys", "values", "get", "copy", "update", "pop", "title"])
def test_an_input_named_like_a_dict_method_renders_its_value(name):
    text = (f"id: x\ninputs:\n  {name}: {{type: string, required: true}}\nnodes:\n"
            f"  - {{id: a, agent: q, prompt: 'Do {{{{ inputs.{name} }}}}', when: inputs.{name} == 'VALUE'}}")
    g = parse_graph_text(text)
    s = engine.new_run(g, coerce_inputs(g, {name: "VALUE"}), "/ws", now=0, run_id="r1")
    assert engine.claim(g, s, now=1) == ["a"]  # the `when` condition saw the value, not a bound method
    assert engine.build_card(g, s, "a")["task"] == "Do VALUE"


def test_dict_methods_are_still_available_when_there_is_no_such_input():
    from agflow.graph import templating

    ctx = {"inputs": {"a": 1, "b": 2}}
    assert templating.render("{% for k, v in inputs.items() %}{{ k }}={{ v }};{% endfor %}", ctx) == "a=1;b=2;"
    assert templating.render("{{ inputs.get('zzz', 'dflt') }} {{ inputs.keys() | list }}", ctx) == "dflt ['a', 'b']"


def test_the_template_sandbox_still_refuses_dunder_access():
    from jinja2.exceptions import SecurityError

    from agflow.graph import templating

    ctx = {"inputs": {"a": 1}}
    for template in ("{{ inputs.__class__ }}", "{{ ''.__class__.__mro__ }}", "{{ range.__globals__ }}"):
        with pytest.raises(SecurityError):
            templating.render(template, ctx)
    # a key that merely *looks* like an attribute is data, never the attribute
    assert templating.render("{{ inputs.__class__ }}", {"inputs": {"__class__": "just data"}}) == "just data"


# --- F27: `advance` renders the nodes' outputs once per pass, with identical results ---------------------------------


def _random_graph_text(rng, size: int) -> str:
    """A random DAG: some nodes carry a `when` over what earlier nodes did, some are gates that may rework."""
    lines = ["id: rnd", "inputs:", "  flag: {type: boolean, default: true}", "  items: {type: string, default: v}",
             "defaults: {retries: 1, max_parallel: 3, timeout_min: 10}", "nodes:"]
    for i in range(size):
        deps = sorted(rng.sample(range(i), k=rng.randint(0, min(i, 3)))) if i else []
        dep_ids = [f"n{d}" for d in deps]
        parts = [f"id: n{i}", f"outputs: ['out-{{{{ run.id }}}}-n{i}']"]
        if dep_ids and rng.random() < 0.25:
            parts += ["type: gate", f"depends_on: [{', '.join(dep_ids)}]"]
            if rng.random() < 0.7:
                parts.append(f"on_fail: {{goto: {dep_ids[0]}, max_loops: 2}}")
        else:
            parts += ["agent: worker"]
            if dep_ids:
                parts.append(f"depends_on: [{', '.join(dep_ids)}]")
            if rng.random() < 0.6:
                dep = rng.choice(dep_ids) if dep_ids else f"n{i}"
                when = rng.choice([
                    "inputs.flag", "inputs.items == 'v'", f"nodes.{dep}.status == 'succeeded'",
                    f"nodes.{dep}.outputs[0] == 'out-r1-{dep}'", f"nodes.{dep}.summary != ''",
                    f"'a.txt' in nodes.{dep}.artifacts", "nodes.missing.status == 'x'", "not inputs.flag",
                    f"nodes.{dep}.status == 'skipped'", f"nodes.{dep}.status in ['skipped', 'succeeded']",
                ])
                parts.append(f"when: \"{when}\"")
        lines.append("  - {" + ", ".join(parts) + "}")
    return "\n".join(lines)


def _drive(graph, rng, *, steps=40):
    """Run a graph to completion with random verdicts; return the state after every step."""
    state = engine.new_run(graph, coerce_inputs(graph, {}), "/ws", now=0, run_id="r1")
    snapshots = [state.model_dump(mode="json")]
    for step in range(1, steps):
        claimed = engine.claim(graph, state, now=float(step))
        for nid in claimed:
            verdict = rng.choice(["succeeded", "succeeded", "failed", "rejected"])
            engine.report(graph, state, nid, verdict, now=float(step) + 0.5,
                          summary=rng.choice(["", "did it"]), artifacts=rng.choice([[], ["a.txt"]]))
        snapshots.append(state.model_dump(mode="json"))
        if state.status != "running":
            break
    return snapshots


@pytest.mark.parametrize("seed", range(60))
def test_advance_gives_the_same_decisions_as_a_fresh_context_for_every_condition(seed, monkeypatch):
    """The pass-local context must equal :func:`engine.context` at every evaluation: same states, same events."""
    import random

    class ReferenceContext:  # the behaviour before F27: build the whole context again for each `when`
        def __init__(self, graph, state):
            self.graph, self.state = graph, state

        def get(self):
            return engine.context(self.graph, self.state)

    graph = parse_graph_text(_random_graph_text(random.Random(seed), size=6 + seed % 7))
    fast = _drive(graph, random.Random(seed * 7 + 1))
    with monkeypatch.context() as mp:
        mp.setattr(engine, "_PassContext", ReferenceContext)
        reference = _drive(graph, random.Random(seed * 7 + 1))
    assert fast == reference


def test_advance_renders_each_outputs_template_once_per_pass(monkeypatch):
    from agflow.graph import templating

    n_when = 6
    lines = ["id: wide", "inputs:", "  flag: {type: boolean, default: true}", "nodes:",
             "  - {id: root, agent: a, outputs: ['o-{{ run.id }}-root', 'p-{{ run.id }}-root']}"]
    for i in range(n_when):  # half of the conditions are true (stay pending), half false (skipped in this pass)
        cond = 'nodes.root.status == "succeeded"' if i % 2 == 0 else "not inputs.flag"
        lines.append(f"  - {{id: w{i}, agent: a, depends_on: [root], outputs: ['o-{{{{ run.id }}}}-w{i}'], "
                     f"when: '{cond}'}}")
    graph = parse_graph_text("\n".join(lines))
    state = engine.new_run(graph, coerce_inputs(graph, {}), "/ws", now=0, run_id="r1")
    engine.claim(graph, state, now=1)
    rendered = []
    real = templating.render

    def counting(template, ctx):
        rendered.append(template)
        return real(template, ctx)

    monkeypatch.setattr(templating, "render", counting)
    engine.report(graph, state, "root", "succeeded", now=2)  # the pass that decides all six conditions
    assert [state.nodes[f"w{i}"].status for i in range(n_when)] == [
        Status.PENDING, Status.SKIPPED, Status.PENDING, Status.SKIPPED, Status.PENDING, Status.SKIPPED]
    templates = 2 + n_when
    assert len(rendered) == templates, f"{len(rendered)} renders for {templates} templates"


def test_input_coercion():
    g = graph()
    assert coerce_inputs(g, {"goal": "g", "ui": "yes"}) == {"goal": "g", "ui": True}
    with pytest.raises(GraphError, match="missing required"):
        coerce_inputs(g, {})
    with pytest.raises(GraphError, match="unknown input"):
        coerce_inputs(g, {"goal": "g", "zzz": 1})
    with pytest.raises(GraphError, match="boolean"):
        coerce_inputs(g, {"goal": "g", "ui": "perhaps"})


def test_progress_marker_changes():
    g, s = run()
    m0 = engine.progress_marker(s)
    engine.claim(g, s, now=1)
    assert engine.progress_marker(s) != m0
