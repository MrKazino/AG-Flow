"""Contract tests against the real google-antigravity SDK: what agflow relies on must hold in the installed package.

These do not call a model. Without the SDK they are skipped; the nightly job sets ``AGFLOW_REQUIRE_SDK=1`` so that an
absent SDK is a failure there instead of a silent skip.
"""

from __future__ import annotations

import asyncio
import os

import pytest

if os.environ.get("AGFLOW_REQUIRE_SDK") == "1":
    import google.antigravity  # noqa: F401  (an ImportError here must fail the run, not skip it)
else:
    pytest.importorskip("google.antigravity")

from google.antigravity import BuiltinTools  # noqa: E402
from google.antigravity.hooks import HookContext, policy  # noqa: E402
from google.antigravity.types import ToolCall  # noqa: E402

from agflow.install import install  # noqa: E402
from agflow.runner.backends import SdkBackend, guard_predicate  # noqa: E402
from tests.test_sdk_backend import card_for  # noqa: E402


def _allowed(policies, tool: str, command: str | None = None) -> bool:
    """Ask the SDK's own enforcement hook whether it would let this tool call through."""
    args = {} if command is None else {"CommandLine": command}
    hook = policy.enforce(policies)
    result = asyncio.run(hook.run(HookContext(), ToolCall(name=tool, args=args)))
    return result.allow


def _both_orders(policies):
    return {"as built": list(policies), "reversed": list(reversed(policies))}


def test_the_sdk_still_documents_the_precedence_agflow_relies_on():
    """If Google rewrites this rule, the comment in ``SdkBackend.build_config`` (and F26's tests) must be revisited."""
    assert "Specific Deny > Specific Ask > Specific Allow >" in policy.__doc__
    assert "Wildcard Deny > Wildcard Ask > Wildcard Allow" in policy.__doc__


@pytest.mark.parametrize("allow_risky", [False, True])
def test_the_guard_deny_outranks_allow_all_whatever_the_list_order(workspace, allow_risky):
    """F26: ``allow_all()`` is a wildcard allow and the guard a specific deny, so the deny wins in either order."""
    install("global", with_gemini_md=False)
    card = card_for(workspace, "bugfix", "review", {"bug": "x"})
    policies = SdkBackend(workspace, allow_risky=allow_risky).build_config(card).policies
    assert policy.allow_all().name in {p.name for p in policies}, "the config under test must contain allow_all()"
    guard = guard_predicate(allow_risky)  # True => deny; 'ask' is answered by nobody in a headless run
    for label, order in _both_orders(policies).items():
        for command in ("rm -rf /", "git reset --hard HEAD~5 && git clean -fdx", "git push --force", "pytest -q", "ls"):
            expected = not guard({"CommandLine": command})
            assert _allowed(order, BuiltinTools.RUN_COMMAND, command) is expected, f"{command!r} ({label})"
        assert not _allowed(order, BuiltinTools.RUN_COMMAND, "rm -rf /"), f"destructive command allowed ({label})"
        assert _allowed(order, BuiltinTools.RUN_COMMAND, "pytest -q"), f"ordinary command refused ({label})"
        assert _allowed(order, BuiltinTools.VIEW_FILE), f"a non-command tool was refused ({label})"


def test_exec_off_denies_every_command_whatever_the_list_order(workspace):
    install("global", with_gemini_md=False)
    card = card_for(workspace, "security-sweep", "report", {})  # researcher: commandExecutionPolicy off
    policies = SdkBackend(workspace, allow_risky=True).build_config(card).policies
    for label, order in _both_orders(policies).items():
        assert not _allowed(order, BuiltinTools.RUN_COMMAND, "pytest -q"), f"exec-off agent ran a command ({label})"
        assert _allowed(order, BuiltinTools.VIEW_FILE), f"exec-off must not block reading files ({label})"


def test_a_specific_allow_cannot_reopen_a_specific_deny():
    """The same rule in miniature, with an unconditional deny: more evidence the order never matters."""
    deny = policy.deny("run_command", name="d", reason="no")
    allow = policy.allow("run_command", name="a")
    for order in ([deny, allow, policy.allow_all()], [policy.allow_all(), allow, deny]):
        assert not _allowed(order, BuiltinTools.RUN_COMMAND, "ls")


# --- F33: the parts of the SDK agflow calls, and a snapshot that shows when the rest of its surface changes ---------

import dataclasses  # noqa: E402
import difflib  # noqa: E402
import importlib.metadata  # noqa: E402
import inspect  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
import types  # noqa: E402
import typing  # noqa: E402

from google.antigravity import Agent, CapabilitiesConfig, LocalAgentConfig  # noqa: E402

from agflow import platform_vocab  # noqa: E402
from tests.conftest import REPO  # noqa: E402

SNAPSHOTS = REPO / "tests" / "snapshots"


def _name(annotation) -> str:
    """A type as text that reads the same on every supported Python (3.10 to 3.14 spell unions and generics alike
    only after this normalisation: ``Union[X, None]``, ``Optional[X]`` and ``X | None`` all become ``X | None``)."""
    if annotation is Ellipsis:
        return "..."
    if annotation is None or annotation is type(None):
        return "None"
    if isinstance(annotation, (list, tuple)):
        return "[" + ", ".join(_name(a) for a in annotation) + "]"
    origin, args = typing.get_origin(annotation), typing.get_args(annotation)
    if origin in (typing.Union, types.UnionType):
        return " | ".join(_name(a) for a in args)
    if origin is not None:
        base = getattr(origin, "__qualname__", str(origin)).replace("typing.", "")
        return f"{base}[{', '.join(_name(a) for a in args)}]" if args else base
    if isinstance(annotation, type):
        return annotation.__qualname__ if annotation.__module__ == "builtins" else (
            f"{annotation.__module__}.{annotation.__qualname__}")
    return str(annotation).replace("typing.", "")


def _fields(model) -> dict[str, str]:
    if hasattr(model, "model_fields"):
        return {name: _name(field.annotation) for name, field in model.model_fields.items()}
    return {f.name: _name(f.type) for f in dataclasses.fields(model)}


def describe_sdk() -> dict:
    """The public surface agflow can depend on, as plain data (so that a change shows up as a readable diff)."""
    from google.antigravity.types import ChatResponse

    response = typing.get_type_hints(Agent.chat).get("return")
    return {
        "LocalAgentConfig": _fields(LocalAgentConfig),
        "CapabilitiesConfig": _fields(CapabilitiesConfig),
        "ToolCall": _fields(ToolCall),
        "BuiltinTools": {member.name: member.value for member in BuiltinTools},
        "policy": {name: str(inspect.signature(fn)) for name, fn in sorted(vars(policy).items())
                   if inspect.isfunction(fn) and not name.startswith("_") and fn.__module__ == policy.__name__},
        "Agent": {"public": sorted(n for n in dir(Agent) if not n.startswith("_")),
                  "async_context_manager": hasattr(Agent, "__aenter__") and hasattr(Agent, "__aexit__"),
                  "chat_returns": getattr(response, "__name__", str(response))},
        "ChatResponse": sorted(n for n in dir(ChatResponse) if not n.startswith("_")),
    }


def _installed_version() -> str:
    return importlib.metadata.version("google-antigravity")


def _snapshot_for(version: str):
    exact = SNAPSHOTS / f"google_antigravity_{version}.json"
    if exact.is_file():
        return exact
    known = sorted(SNAPSHOTS.glob("google_antigravity_*.json"),
                   key=lambda p: tuple(int(x) if x.isdigit() else 0 for x in p.stem.rsplit("_", 1)[1].split(".")))
    return known[-1] if known else None


def test_the_sdk_surface_matches_its_snapshot(request):
    """Fails with a diff when an SDK release adds, removes or renames something agflow could depend on. If the change is
    harmless, regenerate with ``pytest tests/test_sdk_contract.py --update-sdk-snapshot`` and commit the new file."""
    current = json.dumps(describe_sdk(), indent=2, sort_keys=True) + "\n"
    version = _installed_version()
    if request.config.getoption("--update-sdk-snapshot"):
        SNAPSHOTS.mkdir(exist_ok=True)
        (SNAPSHOTS / f"google_antigravity_{version}.json").write_text(current, encoding="utf-8", newline="\n")
        return
    snapshot = _snapshot_for(version)
    assert snapshot is not None, "no SDK snapshot is committed; run pytest --update-sdk-snapshot"
    diff = surface_diff(current, snapshot, version)
    assert not diff, (f"the installed SDK's surface differs from the snapshot (installed {version}):\n{diff}\n"
                      "If agflow still works with it, regenerate with "
                      "`pytest tests/test_sdk_contract.py --update-sdk-snapshot`.")


def surface_diff(current: str, snapshot, version: str) -> str:
    """A unified diff between the snapshot file and the surface read from the installed SDK; empty when equal."""
    expected = snapshot.read_text(encoding="utf-8")
    return "".join(difflib.unified_diff(expected.splitlines(True), current.splitlines(True), snapshot.name,
                                        f"installed google-antigravity {version}"))


def test_a_changed_surface_is_reported_as_a_readable_diff(tmp_path):
    current = json.dumps(describe_sdk(), indent=2, sort_keys=True) + "\n"
    changed = json.loads(current)
    del changed["BuiltinTools"]["RUN_COMMAND"]               # the SDK "added" this since the snapshot
    changed["LocalAgentConfig"]["system_prompt"] = "str"     # and renamed a field
    del changed["LocalAgentConfig"]["system_instructions"]
    snapshot = tmp_path / "google_antigravity_0.0.1.json"
    snapshot.write_text(json.dumps(changed, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    diff = surface_diff(current, snapshot, "9.9.9")
    assert '+    "system_instructions"' in diff and '-    "system_prompt"' in diff
    assert '+    "RUN_COMMAND": "run_command"' in diff
    assert "--- google_antigravity_0.0.1.json" in diff and "+++ installed google-antigravity 9.9.9" in diff
    same = tmp_path / "same.json"
    same.write_text(current, encoding="utf-8")
    assert surface_diff(current, same, "1") == ""


def test_the_newest_snapshot_is_used_when_the_installed_version_has_none(tmp_path, monkeypatch):
    for version in ("0.1.9", "0.1.20", "0.2.0"):
        (tmp_path / f"google_antigravity_{version}.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "SNAPSHOTS", tmp_path)
    assert _snapshot_for("0.1.20").name == "google_antigravity_0.1.20.json"
    assert _snapshot_for("0.3.0").name == "google_antigravity_0.2.0.json"   # numeric, not alphabetical, order
    assert _snapshot_for("0.1.5").name == "google_antigravity_0.2.0.json"


def test_every_sdk_tool_agflow_maps_to_exists_and_finish_is_there():
    """``SdkBackend.build_config`` turns agent-file tool names into these values; a renamed one would drop a tool."""
    values = {member.value for member in BuiltinTools}
    mapped = set(platform_vocab.builtin().sdk_tool_map.values())
    assert mapped <= values, f"agflow maps to SDK tools that no longer exist: {sorted(mapped - values)}"
    assert BuiltinTools.FINISH in set(BuiltinTools) and BuiltinTools.RUN_COMMAND.value == "run_command"
    assert platform_vocab.builtin().command_tool == BuiltinTools.RUN_COMMAND.value, (
        "the guard denies the command tool by name; it must be the SDK's")


def test_the_config_agflow_builds_is_accepted_by_the_sdk_models():
    """The keyword arguments of ``build_config`` must all be fields of the SDK's configuration models."""
    sent = {"system_instructions", "capabilities", "policies", "workspaces", "response_schema", "skills_paths", "model",
            "api_key"}
    assert sent <= set(_fields(LocalAgentConfig)), sorted(sent - set(_fields(LocalAgentConfig)))
    assert {"enable_subagents", "enabled_tools"} <= set(_fields(CapabilitiesConfig))
    config = LocalAgentConfig(system_instructions="x", capabilities=CapabilitiesConfig(
        enable_subagents=False, enabled_tools=[BuiltinTools.FINISH]), policies=[policy.allow_all()],
        workspaces=["."], response_schema=None, skills_paths=[])
    assert config.workspaces and config.capabilities.enabled_tools == [BuiltinTools.FINISH]


def test_the_agent_and_chat_response_have_the_methods_run_awaits():
    from google.antigravity.types import ChatResponse

    assert hasattr(Agent, "__aenter__") and hasattr(Agent, "__aexit__"), "run() uses `async with Agent(config)`"
    assert inspect.iscoroutinefunction(Agent.chat), "run() awaits agent.chat(prompt)"
    assert typing.get_type_hints(Agent.chat).get("return") is ChatResponse
    for name in ("structured_output", "text"):
        assert inspect.iscoroutinefunction(getattr(ChatResponse, name)), f"run() awaits response.{name}()"


def test_the_policy_factories_agflow_uses_exist():
    for name in ("workspace_only", "deny", "allow_all", "enforce"):
        assert callable(getattr(policy, name)), name
    assert policy.deny("run_command", name="n", reason="r", when=lambda args: True).decision.name == "DENY"
