"""Agent backends for the headless runner.

* :class:`SdkBackend`  — runs each node as a fresh Google Antigravity SDK agent configured from the
  node's custom-agent Markdown file (system prompt, model tier, tool allowlist, command policy).
* :class:`FakeBackend` — scripted results for tests, demos and CI dry-runs.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, ValidationError

from agflow import frontmatter
from agflow.hooks import check_command
from agflow.inventory import Inventory
from agflow.models import Kind


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


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_result_text(text: str) -> NodeResult:
    """Extract the final fenced JSON block from an agent reply."""
    for raw in reversed(_JSON_BLOCK.findall(text or "")):
        try:
            return NodeResult.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError):
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

# Antigravity agent-file tool names -> SDK BuiltinTools values.
TOOL_MAP = {
    "view_file": "view_file", "view_file_outline": "view_file", "view_code_item": "view_file",
    "list_dir": "list_directory", "grep_search": "search_directory", "codebase_search": "search_directory",
    "find_by_name": "find_file", "write_to_file": "create_file", "replace_file_content": "edit_file",
    "multi_replace_file_content": "edit_file", "run_command": "run_command", "command_status": "run_command",
    "send_command_input": "run_command", "read_terminal": "run_command", "search_web": "search_web",
    "read_url_content": "read_url_content", "invoke_subagent": "start_subagent",
    "define_subagent": "start_subagent", "generate_image": "generate_image",
}
_CMD_KEYS = ("CommandLine", "command_line", "commandLine", "command", "cmd")


def _command_of(args: dict[str, Any]) -> str:
    for k in _CMD_KEYS:
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

        meta, body = self._agent_definition(card["agent"])
        tools = meta.get("tools")
        caps: dict[str, Any] = {"enable_subagents": bool(tools and "invoke_subagent" in tools)}
        if tools:
            enabled = {BuiltinTools(TOOL_MAP[t]) for t in tools if t in TOOL_MAP}
            enabled.add(BuiltinTools.FINISH)  # needed for structured output
            caps["enabled_tools"] = sorted(enabled, key=lambda t: t.value)
        exec_policy = meta.get("commandExecutionPolicy", "auto")
        policies = [*policy.workspace_only([str(self.workspace)])]
        if exec_policy == "off":
            policies.append(policy.deny("run_command", name="agflow-exec-off",
                                        reason="this agent's commandExecutionPolicy is off"))
        else:
            policies.append(policy.deny("run_command", when=guard_predicate(self.allow_risky), name="agflow-guard",
                                        reason="blocked by the agflow guard (destructive or needs human approval)"))
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
            return await asyncio.wait_for(_go(), timeout=card.get("timeout_min", 30) * 60)
        except asyncio.TimeoutError:
            return NodeResult(status="failed", summary=f"timed out after {card.get('timeout_min')} min")


def model_for_tier(tier: str | None) -> str | None:
    """Map an agent's model tier to a concrete model id via env vars (SDK default when unset)."""
    if tier == "pro":
        return os.environ.get("AGFLOW_MODEL_PRO") or os.environ.get("AGFLOW_MODEL")
    if tier == "flash":
        return os.environ.get("AGFLOW_MODEL_FLASH") or os.environ.get("AGFLOW_MODEL")
    return os.environ.get("AGFLOW_MODEL")
