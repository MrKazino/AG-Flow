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
