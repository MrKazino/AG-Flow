"""Agent backends for the headless runner.

* :class:`SdkBackend`  — runs each node as a fresh Google Antigravity SDK agent configured from the
  node's custom-agent Markdown file (system prompt, model tier, tool allowlist, command policy).
* :class:`FakeBackend` — scripted results for tests, demos and CI dry-runs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, ValidationError

from agflow import frontmatter, platform_vocab
from agflow.graph.schema import DEFAULT_TIMEOUT_MIN, TIMEOUT_MIN_RANGE
from agflow.hooks import check_command
from agflow.inventory import Inventory
from agflow.models import Kind

log = logging.getLogger("agflow.runner")


class RunnerError(RuntimeError):
    pass


class NodeResult(BaseModel):
    """Structured result every node must return (mirrors engine.RESULT_CONTRACT)."""

    status: Literal["succeeded", "failed", "rejected"]
    summary: str = Field(description="What was done and the evidence, at most 6 sentences.")
    artifacts: list[str] = Field(default_factory=list, description="Files created or changed.")
    followups: list[str] = Field(default_factory=list, description="Optional next steps.")


class AgentBackend(Protocol):
    name: str

    async def run(self, card: dict[str, Any]) -> NodeResult: ...


_FENCE_OPEN = re.compile(r"```(?:json)?\s*(?=[\[{])")
_FENCE_CLOSE = re.compile(r"\s*```")
_DECODER = json.JSONDecoder()


def _fenced_json_blocks(text: str) -> list[Any]:
    """Every JSON value that sits alone inside a fence, in reading order.

    The value is read with a real JSON parser rather than a pattern, so a ``}`` or a fence inside a string value cannot
    end it early; the scan resumes after the value, so a fence quoted inside a string is never mistaken for an opener.
    """
    found: list[Any] = []
    pos = 0
    while (opener := _FENCE_OPEN.search(text, pos)) is not None:
        try:
            value, end = _DECODER.raw_decode(text, opener.end())
        except (ValueError, RecursionError):
            pos = opener.end()
            continue
        pos = end
        if _FENCE_CLOSE.match(text, end):
            found.append(value)
    return found


def parse_result_text(text: str) -> NodeResult:
    """Extract the final fenced JSON result from an agent reply (an object, or the last valid object of an array)."""
    for value in reversed(_fenced_json_blocks(text or "")):
        for candidate in reversed(value) if isinstance(value, list) else [value]:
            try:
                return NodeResult.model_validate(candidate)
            except ValidationError:
                continue
    tail = (text or "").strip()[-600:]
    return NodeResult(status="failed", summary=f"agent reply had no valid result JSON block. Tail: {tail}")


# --- fake -------------------------------------------------------------------------------------


class FakeBackend:
    """Returns scripted results: ``{node_id: [result_for_attempt_1, result_for_attempt_2, ...]}``."""

    name = "fake"

    def __init__(self, script: dict[str, list[dict[str, Any]]] | None = None, delay: float = 0.0,
                 on_run: Callable[[dict[str, Any]], None] | None = None):
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.delay = delay
        self.on_run = on_run
        self.calls: list[dict[str, Any]] = []

    async def run(self, card: dict[str, Any]) -> NodeResult:
        self.calls.append(card)
        if self.on_run:
            self.on_run(card)
        if self.delay:
            await asyncio.sleep(self.delay)
        queue = self.script.get(card["node_id"])
        if queue:
            return NodeResult.model_validate(queue.pop(0))
        return NodeResult(status="succeeded", summary=f"[fake] {card['title']} completed",
                          artifacts=list(card.get("expected_outputs", [])))


# --- Antigravity SDK ------------------------------------------------------------------------------

# Antigravity agent-file tool names -> SDK BuiltinTools values. The mapping lives in platform_vocab (versioned, and
# overridable without a release); this alias is the built-in table, kept for compatibility. build_config uses the
# effective table, overrides included.
TOOL_MAP = dict(platform_vocab.builtin().sdk_tool_map)


def _command_of(args: dict[str, Any]) -> str:
    for k in platform_vocab.current().keys("command"):
        if isinstance(args.get(k), str):
            return args[k]
    return ""


def guard_predicate(allow_risky: bool) -> Callable[[dict[str, Any]], bool]:
    """True => deny. Headless runs have nobody to answer 'ask', so 'ask' also denies unless allow_risky."""

    def predicate(args: dict[str, Any]) -> bool:
        decision = check_command(_command_of(args or {})).get("decision")
        return decision == "deny" or (decision == "ask" and not allow_risky)

    return predicate


class SdkBackend:
    name = "sdk"

    def __init__(self, workspace: Path, inventory: Inventory | None = None, *, allow_risky: bool = False,
                 api_key: str | None = None):
        try:
            import google.antigravity  # noqa: F401
        except ImportError as exc:
            raise RunnerError("the SDK backend needs the Antigravity SDK: pip install 'agflow[sdk]' "
                              "(or: uv tool install 'agflow[sdk]')") from exc
        self.workspace = workspace
        self.inventory = inventory or Inventory.discover(workspace, include_legacy=False)
        self.allow_risky = allow_risky
        self.api_key = api_key

    def _agent_definition(self, name: str) -> tuple[dict[str, Any], str]:
        item = self.inventory.find(Kind.AGENT, name)
        if item is None:
            raise RunnerError(f"custom agent '{name}' not found (install the agflow plugin or add "
                              f".agents/agents/{name}.md)")
        return frontmatter.parse(item.path.read_text(encoding="utf-8"))

    def _skills_paths(self) -> list[str]:
        return sorted({str(i.path.parent.parent) for i in self.inventory.of(Kind.SKILL)})

    def build_config(self, card: dict[str, Any]) -> Any:
        from google.antigravity import BuiltinTools, CapabilitiesConfig, LocalAgentConfig
        from google.antigravity.hooks import policy

        vocab = platform_vocab.current(self.workspace)
        meta, body = self._agent_definition(card["agent"])
        tools = meta.get("tools")
        caps: dict[str, Any] = {"enable_subagents": bool(tools and vocab.subagent_tool in tools)}
        if tools:
            # Tools Antigravity knows but the SDK has no equivalent for (UI-only ones) are normal. A name the
            # vocabulary has never heard of may be a rename: say so instead of silently dropping the capability.
            unknown = sorted(t for t in tools if t not in vocab.agent_tools and not t.startswith("mcp"))
            if unknown:
                log.warning("agent '%s': tool(s) %s are unknown to this agflow version (%s) and are not given to the "
                            "headless agent; if Antigravity renamed them, extend the table in vocab.json",
                            card["agent"], ", ".join(unknown), vocab.describe())
            enabled = {BuiltinTools(vocab.sdk_tool_map[t]) for t in tools if t in vocab.sdk_tool_map}
            enabled.add(BuiltinTools.FINISH)  # needed for structured output
            caps["enabled_tools"] = sorted(enabled, key=lambda t: t.value)
        exec_policy = meta.get("commandExecutionPolicy", "auto")
        policies = [*policy.workspace_only([str(self.workspace)])]
        if exec_policy == "off":
            policies.append(policy.deny(vocab.command_tool, name="agflow-exec-off",
                                        reason="this agent's commandExecutionPolicy is off"))
        else:
            policies.append(policy.deny(vocab.command_tool, when=guard_predicate(self.allow_risky), name="agflow-guard",
                                        reason="blocked by the agflow guard (destructive or needs human approval)"))
        # The list order is irrelevant: the SDK ranks "Specific Deny > Specific Ask > Specific Allow > Wildcard Deny >
        # Wildcard Ask > Wildcard Allow" (google/antigravity/hooks/policy.py, module docstring). allow_all() is a
        # wildcard allow, so it can never outrank the specific run_command deny above, wherever it is placed.
        # tests/test_sdk_contract.py runs both orders against the installed SDK and watches that docstring.
        policies.append(policy.allow_all())
        kwargs: dict[str, Any] = {
            "system_instructions": body.strip(),
            "capabilities": CapabilitiesConfig(**caps),
            "policies": policies,
            "workspaces": [str(self.workspace)],
            "response_schema": NodeResult,
            "skills_paths": self._skills_paths(),
        }
        model = model_for_tier(meta.get("model"))
        if model:
            kwargs["model"] = model
        if self.api_key:
            kwargs["api_key"] = self.api_key
        return LocalAgentConfig(**kwargs)

    async def run(self, card: dict[str, Any]) -> NodeResult:
        from google.antigravity import Agent

        config = self.build_config(card)

        async def _go() -> NodeResult:
            async with Agent(config) as agent:
                response = await agent.chat(card["subagent_prompt"])
                structured = await response.structured_output()
                if structured is not None:
                    data = structured.model_dump() if isinstance(structured, BaseModel) else structured
                    try:
                        return NodeResult.model_validate(data)
                    except ValidationError:
                        pass
                return parse_result_text(await response.text())

        try:
            return await asyncio.wait_for(_go(), timeout=timeout_seconds(card))
        except asyncio.TimeoutError:
            return NodeResult(status="failed", summary=f"node '{card.get('node_id', '?')}' timed out after "
                                                       f"{timeout_seconds(card) / 60:g} min")


def timeout_seconds(card: dict[str, Any]) -> float:
    """How long a node may run: the card's ``timeout_min`` within the graph schema's bounds, else the default.

    A hand-built card may omit it, set it to ``None`` or to something that is not a number; none of those may stop the
    node from running, and a bool is not a number of minutes.
    """
    minutes = card.get("timeout_min")
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or not math.isfinite(minutes):
        minutes = DEFAULT_TIMEOUT_MIN
    low, high = TIMEOUT_MIN_RANGE
    return float(min(max(minutes, low), high) * 60)


def model_for_tier(tier: str | None) -> str | None:
    """Map an agent's model tier to a concrete model id via env vars (SDK default when unset)."""
    if tier in platform_vocab.current().model_tiers():  # every tier in the vocabulary has its own AGFLOW_MODEL_<TIER>
        return os.environ.get(f"AGFLOW_MODEL_{tier.upper()}") or os.environ.get("AGFLOW_MODEL")
    return os.environ.get("AGFLOW_MODEL")
