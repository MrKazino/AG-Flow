"""F28: the CI workflow keeps its coverage floor and its Windows/macOS jobs, and tests do not wait on the clock."""

from __future__ import annotations

import re

from agflow.fsutil import yaml_safe
from tests.conftest import REPO

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10: pytest itself depends on tomli there
    import tomli as tomllib

CI =REPO / ".github" / "workflows" / "ci.yml"


def _test_job() -> dict:
    return yaml_safe().load(CI.read_text(encoding="utf-8"))["jobs"]["test"]


def test_ci_runs_the_tests_on_all_three_systems():
    matrix = _test_job()["strategy"]["matrix"]
    systems = {*matrix["os"], *(entry["os"] for entry in matrix.get("include", []))}
    assert {"ubuntu-latest", "windows-latest", "macos-latest"} <= systems, systems
    assert _test_job()["runs-on"] == "${{ matrix.os }}"
    assert _test_job()["defaults"]["run"]["shell"] == "bash", "the steps are bash scripts; Windows needs it said"


def test_ci_gates_coverage_in_exactly_one_job_and_the_gate_is_not_in_addopts():
    gated = [s for s in _test_job()["steps"] if "--cov-fail-under" in str(s.get("run", ""))]
    assert len(gated) == 1, "one reference job carries the floor (coverage differs per system)"
    floor = int(re.search(r"--cov-fail-under=(\d+)", gated[0]["run"]).group(1))
    assert floor >= 84, f"the floor was lowered to {floor}"
    assert "--cov=agflow" in gated[0]["run"]
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert "cov-fail-under" not in str(pyproject["tool"]["pytest"]["ini_options"].get("addopts", "")), (
        "a floor in addopts would fail every single-file run on a developer's machine")
    assert any(dep.startswith("pytest-cov") for dep in pyproject["dependency-groups"]["dev"])
    assert pyproject["tool"]["coverage"]["run"]["source"] == ["agflow"]


def test_every_other_test_step_runs_the_plain_suite():
    steps = {s["name"]: s for s in _test_job()["steps"] if "name" in s}
    plain = steps["Tests"]
    assert plain["run"].strip() == "uv run pytest -q"
    assert "!(" in plain["if"], "the plain run must be the complement of the coverage run"


def test_no_test_waits_for_the_clock():
    """A sleep in a test is a guess about how long something takes: it flakes on a slow runner and wastes time on a
    fast one. Wait for an event, or set the file time explicitly."""
    needle = re.compile(r"\b(?:time|asyncio)\.sleep\(")
    offenders = []
    for path in sorted((REPO / "tests").rglob("*.py")):
        if path.name == "test_ci_config.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if needle.search(line.split("#")[0]):
                offenders.append(f"{path.relative_to(REPO)}:{number}: {line.strip()}")
    assert offenders == []


# --- F33: the nightly SDK job ----------------------------------------------------------------------------------------

NIGHTLY = REPO / ".github" / "workflows" / "sdk-contract.yml"


def _nightly() -> dict:
    return yaml_safe().load(NIGHTLY.read_text(encoding="utf-8"))


def test_the_nightly_sdk_job_runs_on_a_schedule_and_on_demand_and_requires_the_sdk():
    workflow = _nightly()
    triggers = workflow.get("on") or workflow.get(True)  # YAML 1.1 reads a bare `on` as the boolean True
    assert "schedule" in triggers and "workflow_dispatch" in triggers
    job = workflow["jobs"]["contract"]
    assert job["env"]["AGFLOW_REQUIRE_SDK"] == "1", "a missing SDK must fail the job, not skip the tests"
    text = NIGHTLY.read_text(encoding="utf-8")
    assert "uv pip install --upgrade google-antigravity" in text, "the point is to test the newest release"
    assert "uv run --no-sync" in text, "a later `uv run` would re-sync the lock file and undo the upgrade"
    for tests in ("test_sdk_contract.py", "test_sdk_backend.py", "test_sdk_run.py", "test_report_schema.py"):
        assert tests in text


def test_the_live_step_cannot_pass_silently_without_the_key():
    live = next(s for s in _nightly()["jobs"]["contract"]["steps"] if s.get("name", "").startswith("Live run"))
    script = live["run"]
    assert "GOOGLE_API_KEY" in str(live["env"]["GEMINI_API_KEY"]), "the secret feeds the SDK's variable"
    assert "::warning" in script, "a skipped live run must be visible"
    assert 'workflow_dispatch" ]; then exit 1' in script, "a manual run exists to do the live check: skipping it fails"
    assert "--backend sdk" in script and "tests/fixtures/sdk_smoke_graph.yaml" in script
    assert "scripts/report_schema.py" in script and "run_report_keys.json" in script


def test_the_smoke_graph_is_a_valid_one_node_graph_with_a_bundled_agent():
    from agflow.graph.schema import load_graph

    graph = load_graph(REPO / "tests" / "fixtures" / "sdk_smoke_graph.yaml")
    assert [n.id for n in graph.nodes] == ["check"] and graph.nodes[0].agent == "researcher"
    assert (REPO / "plugin" / "agents" / "researcher.md").is_file()
