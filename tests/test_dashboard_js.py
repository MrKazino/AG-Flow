"""The dashboard's browser code: stale responses must not overwrite newer views (F20), and no HTML sinks (F25)."""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from tests.conftest import REPO

STATIC = REPO / "src" / "agflow" / "dashboard" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed (CI has it)")
def test_node_unit_tests_for_the_latest_wins_guard():
    files = sorted(str(p) for p in (REPO / "tests" / "js").glob("*.test.mjs"))  # explicit: newer Node won't take a dir
    assert files
    proc = subprocess.run(["node", "--test", *files], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_guard_script_is_loaded_before_the_app():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert html.index("/static/seq.js") < html.index("/static/app.js")


def test_the_router_ignores_responses_that_arrive_after_a_newer_navigation():
    route = APP_JS[APP_JS.index("async function route()"):]
    route = route[:route.index("\n  }\n") + 5]
    assert "AgLatest.latest()" in APP_JS, "the router needs a latest-wins guard"
    assert "routeGuard.begin()" in route and "isCurrent(token)" in route
    # the page is awaited, then checked, and only then rendered: a stale result is dropped, never shown
    awaited = route.index("await pages[name]")
    assert awaited < route.index("ctx.current()", awaited) < route.index("$view.replaceChildren(view)")


def test_navigating_away_cancels_in_flight_reads():
    assert "new AbortController()" in APP_JS and "routeAbort.abort()" in APP_JS
    assert re.search(r"signal:\s*[^,}]*routeSignal", APP_JS), "api() must pass the route's signal on reads"
    assert "AbortError" in APP_JS, "an aborted read is expected, not an error to show"


def test_a_superseded_run_page_does_not_open_a_live_stream():
    run_detail = APP_JS[APP_JS.index("async function runDetail("):]
    run_detail = run_detail[:run_detail.index("// ---------- router")]
    assert run_detail.index("ctx.current()") < run_detail.index("liveStream(")


def test_the_graph_editor_validation_is_latest_wins():
    graphs = APP_JS[APP_JS.index("pages.graphs"):APP_JS.index("let stream = null")]
    assert "AgLatest.latest()" in graphs and "isCurrent(" in graphs


def test_the_overview_page_shows_the_safety_guard_alarm():
    overview = APP_JS[APP_JS.index("pages.overview"):APP_JS.index("pages.doctor")]
    assert "o.hook_health" in overview and "Safety guard unavailable" in overview


# --- F25: nothing in the dashboard's scripts may turn a string into markup or code ----------------------------------

_SINKS = ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "setTimeout(\"",
          "setInterval(\"")


def _code_only(source: str) -> str:
    """The script without its comments (a ``//`` that follows a colon or a quote is part of a URL or string)."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"(?<![:\"'\w])//.*", "", source)


def test_no_script_of_the_dashboard_contains_an_html_or_code_sink():
    scripts = sorted(STATIC.glob("*.js"))
    assert {p.name for p in scripts} >= {"app.js", "graph.js", "seq.js"}
    found = {p.name: [s for s in _SINKS if s in _code_only(p.read_text(encoding="utf-8"))] for p in scripts}
    assert not any(found.values()), f"HTML/code sinks in the dashboard scripts: {found}"


def test_the_sink_scan_sees_through_comments_and_urls_but_not_code():
    assert _SINKS[0] not in _code_only("// el.innerHTML = x\n/* el.innerHTML = y */\nconst a = 1;")
    assert _SINKS[0] in _code_only('const NS = "http://www.w3.org/2000/svg"; e.innerHTML = v;')
    assert "eval(" in _code_only("x = eval(y) // trailing comment")


def test_the_element_helper_cannot_inject_markup():
    helper = APP_JS[APP_JS.index("function h(tag, props"):]
    helper = helper[:helper.index("\n  }\n") + 5]
    assert "createTextNode" in helper, "children must be added as text nodes"
    assert '"html"' not in helper, "h() must not offer an html: property"
