"""`SdkBackend.run` against a stand-in for the Antigravity SDK (F21, F23).

No SDK has to be installed and no model is called: ``google.antigravity`` is replaced by a tiny module whose ``Agent``
is an async context manager with ``chat()``; the tests decide what it answers. ``build_config`` is replaced by a
sentinel so that ``run`` is tested on its own (its real output is covered by ``test_sdk_backend.py`` and, against the
installed package, ``test_sdk_contract.py``).
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest
from pydantic import BaseModel, ValidationError

from agflow.graph.schema import TIMEOUT_MIN_RANGE, Defaults
from agflow.inventory import Inventory
from agflow.runner import backends
from agflow.runner.backends import NodeResult, RunnerError, SdkBackend, timeout_seconds

CONFIG = object()  # what the stubbed build_config returns; Agent must receive exactly this


class Reply:
    """What ``agent.chat()`` returns: ``structured_output()`` (a model, a dict, ``None``…) and ``text()``."""

    def __init__(self, structured=None, text=""):
        self._structured, self._text = structured, text

    async def structured_output(self):
        return self._structured

    async def text(self):
        return self._text


class FakeAgent:
    """Stands in for ``google.antigravity.Agent``. Tests set ``reply`` (async, takes the prompt) per case."""

    configs: list = []
    prompts: list = []
    entered = exited = 0
    reply = None

    def __init__(self, config):
        FakeAgent.configs.append(config)

    async def __aenter__(self):
        FakeAgent.entered += 1
        return self

    async def __aexit__(self, *exc):
        FakeAgent.exited += 1
        return False

    async def chat(self, prompt):
        FakeAgent.prompts.append(prompt)
        return await FakeAgent.reply(prompt)


@pytest.fixture
def sdk(monkeypatch, workspace):
    """The fake SDK in place, plus a backend wired to the sentinel config and an empty inventory."""
    google = types.ModuleType("google")
    google.__path__ = []  # a package, so that `import google.antigravity` resolves
    antigravity = types.ModuleType("google.antigravity")
    antigravity.Agent = FakeAgent
    google.antigravity = antigravity
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.antigravity", antigravity)
    monkeypatch.setattr(SdkBackend, "build_config", lambda self, card: CONFIG)
    FakeAgent.configs, FakeAgent.prompts, FakeAgent.entered, FakeAgent.exited = [], [], 0, 0
    FakeAgent.reply = None
    backend = SdkBackend(workspace, inventory=Inventory(workspace=workspace))

    def run(card, reply):
        FakeAgent.reply = reply
        return asyncio.run(backend.run(card))

    backend.run_sync = run
    return backend


def card(**kw):
    return {"node_id": "review", "agent": "reviewer", "title": "Review", "subagent_prompt": "DO THE TASK", **kw}


def replying(structured=None, text=""):
    async def reply(prompt):
        return Reply(structured, text)

    return reply


class Other(BaseModel):
    status: str = "nope"


# --- the branches of run() ------------------------------------------------------------------------------------------


def test_a_structured_result_is_returned_as_is(sdk):
    structured = NodeResult(status="succeeded", summary="all good", artifacts=["a.py"])
    result = sdk.run_sync(card(), replying(structured, text="ignored"))
    assert result == structured


def test_a_structured_dict_is_validated(sdk):
    result = sdk.run_sync(card(), replying({"status": "rejected", "summary": "needs work", "followups": ["x"]}))
    assert (result.status, result.summary, result.followups) == ("rejected", "needs work", ["x"])


@pytest.mark.parametrize("structured", [{"status": "great", "summary": "bad status"}, {"summary": "no status"},
                                        Other(), "just a string", 42])
def test_an_invalid_structured_result_falls_back_to_the_text_block(sdk, structured):
    text = 'done\n```json\n{"status": "succeeded", "summary": "from the text"}\n```'
    result = sdk.run_sync(card(), replying(structured, text=text))
    assert (result.status, result.summary) == ("succeeded", "from the text")


def test_no_structured_result_uses_the_text_block(sdk):
    text = '```json\n{"status": "failed", "summary": "could not"}\n```'
    assert sdk.run_sync(card(), replying(None, text=text)).status == "failed"


def test_a_reply_without_any_result_is_a_failure_that_shows_the_tail(sdk):
    result = sdk.run_sync(card(), replying(None, text="I did some things but forgot the block."))
    assert result.status == "failed" and "forgot the block" in result.summary


def test_the_prompt_reaches_the_agent_verbatim_and_the_agent_gets_the_built_config(sdk):
    sdk.run_sync(card(subagent_prompt="exact\n  prompt  "), replying(NodeResult(status="succeeded", summary="s")))
    assert FakeAgent.prompts == ["exact\n  prompt  "]
    assert FakeAgent.configs == [CONFIG]


def test_the_agent_is_always_closed_even_when_it_fails(sdk):
    async def boom(prompt):
        raise RuntimeError("model unreachable")

    with pytest.raises(RuntimeError, match="model unreachable"):
        sdk.run_sync(card(), boom)
    assert (FakeAgent.entered, FakeAgent.exited) == (1, 1)  # the runner turns this into a failed attempt


def test_a_config_that_cannot_be_built_stops_before_any_agent_exists(sdk, monkeypatch):
    def broken(self, card):
        raise RunnerError("custom agent 'reviewer' not found")

    monkeypatch.setattr(SdkBackend, "build_config", broken)
    with pytest.raises(RunnerError, match="not found"):
        sdk.run_sync(card(), replying())
    assert FakeAgent.configs == [] and FakeAgent.entered == 0


def test_a_real_timeout_cancels_the_agent_and_names_the_node(sdk, monkeypatch):
    cancelled = []

    async def never(prompt):
        try:
            await asyncio.Event().wait()  # an agent that never answers; only cancellation ends it
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    monkeypatch.setattr(backends, "timeout_seconds", lambda card: 0.05)
    result = sdk.run_sync(card(node_id="slow-node"), never)
    assert result.status == "failed" and result.summary.startswith("node 'slow-node' timed out after ")
    assert cancelled == [True], "the agent's work must be cancelled, not abandoned"
    assert FakeAgent.exited == 1


def test_a_card_with_no_timeout_still_runs(sdk):
    """F23: ``None * 60`` raised an unexplained TypeError before the agent was even started."""
    result = sdk.run_sync(card(timeout_min=None), replying(NodeResult(status="succeeded", summary="ran")))
    assert result.summary == "ran"


def test_a_timeout_names_the_node_and_the_minutes_it_was_given(sdk, monkeypatch):
    async def expire(awaitable, timeout):
        awaitable.close()  # the real wait_for would cancel it; here the time simply runs out
        raise asyncio.TimeoutError

    monkeypatch.setattr(backends.asyncio, "wait_for", expire)
    result = sdk.run_sync(card(timeout_min=7), replying())
    assert result.summary == "node 'review' timed out after 7 min"


# --- construction ---------------------------------------------------------------------------------------------------


def test_a_missing_sdk_is_a_clear_error(workspace, monkeypatch):
    monkeypatch.setitem(sys.modules, "google.antigravity", None)  # makes `import google.antigravity` fail
    with pytest.raises(RunnerError, match=r"agflow\[sdk\]"):
        SdkBackend(workspace)


def test_an_unknown_agent_is_a_clear_error(sdk):
    with pytest.raises(RunnerError, match="custom agent 'ghost' not found"):
        sdk._agent_definition("ghost")


# --- the timeout helper ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("minutes", "seconds"), [
    (7, 420.0), (2.5, 150.0), (1, 60.0), (1440, 86400.0),                  # inside the schema's bounds
    (0, 60.0), (-5, 60.0), (10**6, 86400.0), (float("inf"), 1800.0),       # clamped to [1, 1440] minutes, or default
    (None, 1800.0), ("ten", 1800.0), ("7", 1800.0), (True, 1800.0), (False, 1800.0), (float("nan"), 1800.0),
    ([5], 1800.0),
])
def test_timeout_seconds_never_fails_and_stays_within_the_schema_bounds(minutes, seconds):
    assert timeout_seconds({"timeout_min": minutes}) == seconds


def test_a_card_without_a_timeout_gets_the_default():
    assert timeout_seconds({}) == 30 * 60


def test_the_runner_clamp_matches_the_graph_schema():
    """The bounds are one constant: a value the schema accepts is never altered by the runner, and vice versa."""
    low, high = TIMEOUT_MIN_RANGE
    for ok in (low, high):
        assert Defaults(timeout_min=ok).timeout_min == ok and timeout_seconds({"timeout_min": ok}) == ok * 60
    for bad in (low - 1, high + 1):
        with pytest.raises(ValidationError):
            Defaults(timeout_min=bad)
