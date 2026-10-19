"""The release workflow stays what the maintainer reviewed: tag-triggered, least privilege, checked first."""

from __future__ import annotations

import re

import pytest

from agflow.fsutil import yaml_safe
from tests.conftest import REPO

WORKFLOW = REPO / ".github" / "workflows" / "release.yml"


@pytest.fixture(scope="module")
def flow() -> dict:
    return yaml_safe().load(WORKFLOW.read_text(encoding="utf-8"))


def steps(flow) -> list[dict]:
    return flow["jobs"]["release"]["steps"]


def test_only_a_version_tag_triggers_a_release(flow):
    on = flow.get("on", flow.get(True))   # YAML 1.1 reads a bare `on` as True
    assert set(on) == {"push"} and on["push"] == {"tags": ["v*"]}


def test_permissions_are_the_three_a_release_needs(flow):
    assert flow["permissions"] == {"contents": "write", "id-token": "write", "attestations": "write"}


def test_the_release_is_checked_before_it_is_published(flow):
    runs = [str(s.get("run", "")) for s in steps(flow)]
    order = {name: next(i for i, run in enumerate(runs) if name in run)
             for name in ("pytest -q", "make_release.py", "smoke_release.py", "gh release create")}
    assert order["pytest -q"] < order["make_release.py"] < order["smoke_release.py"] < order["gh release create"]
    attest = next(i for i, s in enumerate(steps(flow)) if "attest-build-provenance" in str(s.get("uses", "")))
    assert order["smoke_release.py"] < attest < order["gh release create"]


def test_the_tag_must_match_the_version_in_the_code(flow):
    guard = next(s for s in steps(flow) if "matches the version" in s.get("name", ""))
    assert "GITHUB_REF_NAME" in guard["run"] and "exit 1" in guard["run"]


def test_only_known_actions_and_only_the_default_token_are_used(flow):
    used = {str(s["uses"]).split("@")[0] for s in steps(flow) if "uses" in s}
    assert used <= {"actions/checkout", "astral-sh/setup-uv", "actions/attest-build-provenance"}, used
    text = WORKFLOW.read_text(encoding="utf-8")
    assert set(re.findall(r"secrets\.(\w+)", text)) == {"GITHUB_TOKEN"}
    assert "pull_request" not in text, "a fork's pull request must never reach a job with write permission"


def test_the_scripts_it_calls_exist_and_the_release_files_are_uploaded(flow):
    text = WORKFLOW.read_text(encoding="utf-8")
    for script in re.findall(r"scripts/(\w+\.py)", text):
        assert (REPO / "scripts" / script).is_file(), script
    assert "dist/release/*" in text and flow["jobs"]["release"]["timeout-minutes"] <= 60
