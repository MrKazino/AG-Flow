"""Secret redaction (F11): run artifacts meant for logs and reports must not carry credentials."""

from __future__ import annotations

import copy
import json

import pytest

from agflow import redact
from agflow.graph import engine
from agflow.graph.schema import coerce_inputs, parse_graph_text
from agflow.graph.store import RunStore

SECRET = "sk-live-SECRET1234567890abcdef"
GRAPH = """
id: secretish
inputs:
  api_key: {type: string, required: true}
  feature: {type: string, default: demo}
nodes:
  - id: a
    agent: implementer
    prompt: "Call the service with {{ inputs.api_key }} for {{ inputs.feature }}"
"""


@pytest.mark.parametrize("key", ["api_key", "API_KEY", "apiKey", "GITHUB_TOKEN", "password", "db-password",
                                 "client_secret", "Authorization", "auth_header", "credentials"])
def test_secret_looking_keys_are_redacted(key):
    assert redact.redact_mapping({key: "hunter2"}) == {key: redact.REDACTED}


@pytest.mark.parametrize("key", ["feature", "bug", "version", "author", "authors", "title", "has_ui"])
def test_ordinary_keys_are_left_alone(key):
    assert redact.redact_mapping({key: "value"}) == {key: "value"}


def test_nested_structures_are_walked_and_the_input_is_not_mutated():
    original = {"service": {"url": "https://x", "token": "abc", "tags": ["a", {"password": "p"}]}, "n": 3}
    snapshot = copy.deepcopy(original)
    assert redact.redact_mapping(original) == {
        "service": {"url": "https://x", "token": redact.REDACTED, "tags": ["a", {"password": redact.REDACTED}]}, "n": 3}
    assert original == snapshot


def test_a_secret_key_hides_a_whole_structured_value():
    assert redact.redact_mapping({"auth": {"user": "u", "pass": "p"}}) == {"auth": redact.REDACTED}


@pytest.mark.parametrize("secret", [
    "sk-live-SECRET1234567890abcdef", "ghp_abcdefghijklmnopqrstuvwxyz0123456789", "github_pat_11ABCDEFG0123456789_abc",
    "AIzaSyA-1234567890abcdefghijklmnopqrstuv", "xoxb-1234567890-abcdefghij", "AKIAABCDEFGHIJKLMNOP",
    "Bearer abcdefghijklmnop.qrstuvwxyz-0123456789", "-----BEGIN RSA PRIVATE KEY-----"])
def test_well_known_token_shapes_are_redacted_even_under_an_innocent_key(secret):
    out = redact.redact_mapping({"note": f"please use {secret} here"})
    assert secret not in out["note"] and redact.REDACTED in out["note"] and out["note"].startswith("please use")


def test_empty_values_and_non_strings_are_untouched():
    data = {"api_key": "", "token": None, "password": False, "count": 0}
    assert redact.redact_mapping(data) == data


def test_redaction_is_idempotent():
    once = redact.redact_mapping({"api_key": "x", "note": f"k={SECRET}"})
    assert redact.redact_mapping(once) == once


def test_validate_still_exposes_the_shared_pattern():
    from agflow import validate

    assert validate.SECRET_KEY_RE is redact.SECRET_KEY_RE


# --- the places a run writes inputs down ---------------------------------------------------------------


def start(workspace):
    graph = parse_graph_text(GRAPH)
    store = RunStore(workspace)
    state = store.create(graph, coerce_inputs(graph, {"api_key": SECRET}))
    return graph, store, state


def test_ledger_never_holds_the_secret(workspace):
    _, store, state = start(workspace)
    ledger = (store.run_dir(state.run_id) / "ledger.jsonl").read_text(encoding="utf-8")
    assert SECRET not in ledger
    started = json.loads(ledger.splitlines()[0])
    assert started["inputs"] == {"api_key": redact.REDACTED, "feature": "demo"}


def test_reports_never_hold_the_secret_but_state_and_prompt_still_do(workspace):
    graph, store, state = start(workspace)
    # the engine needs the real value: it renders `{{ inputs.api_key }}` into the subagent prompt
    assert SECRET in store.load(state.run_id).inputs.values()
    claimed = engine.claim(graph, state, now=1.0)
    assert SECRET in engine.build_card(graph, state, claimed[0])["subagent_prompt"]
    report_json, report_md = store.write_report(store.load(state.run_id))
    assert SECRET not in report_json.read_text(encoding="utf-8")
    assert SECRET not in report_md.read_text(encoding="utf-8")
    assert redact.REDACTED in report_md.read_text(encoding="utf-8")


def test_nothing_but_state_json_in_the_run_folder_holds_the_secret(workspace):
    _, store, state = start(workspace)
    store.write_report(store.load(state.run_id))
    holders = [p.name for p in store.run_dir(state.run_id).iterdir()
               if p.is_file() and SECRET in p.read_text(encoding="utf-8", errors="replace")]
    assert holders == ["state.json"]
