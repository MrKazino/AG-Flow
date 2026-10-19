"""The Antigravity vocabulary agflow depends on, in one place, with an explicit version.

Antigravity's agent tool names, model tiers, execution policies, rule triggers, hook events, hook-payload keys and
the retirement date of workflows are not a published, versioned contract. They used to be constants scattered over
``validate.py``, ``runner/backends.py``, ``hooks.py`` and ``fixes.py``, so a rename on Google's side silently broke
the SDK tool mapping or the doctor, and fixing it meant a new agflow release.

Now:

* the built-in table is the packaged data file ``data/platform_vocab.json`` (it carries ``vocab_version``);
* everything else derives from it through :func:`builtin` / :func:`current` -- no other module lists these names;
* a user can *extend or correct* it without a release, with an optional override file
  (``<global agflow state>/vocab.json`` and ``<workspace>/.agents/agflow/vocab.json``). An invalid override is
  ignored as a whole and reported by ``agflow doctor`` (``vocab.override.invalid``), never applied half-way.

Override file (every key optional)::

    {"vocab_version": "my-2026.11",
     "agent_tools":     {"add": ["new_tool"], "remove": ["old_tool"]},
     "sdk_tool_map":    {"set": {"new_tool": "view_file"}, "remove": ["old_tool"]},
     "agent_models":    {"add": ["ultra"]},      # same add/remove shape: exec_policies, rule_triggers, hook_events,
                                                 # hook_events_grouped, command_tools, file_tools, default_agent_tools
     "hook_payload_keys": {"tool_name": {"add": ["toolId"]}},
     "workflows_retired_on": "2027-01-01"}

Stdlib only: the hook fast path imports this module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from agflow import paths

DATA_FILE = Path(__file__).resolve().parent / "data" / "platform_vocab.json"
OVERRIDE_NAME = "vocab.json"

_LIST_FIELDS = ("agent_tools", "default_agent_tools", "command_tools", "file_tools", "agent_models", "exec_policies",
                "rule_triggers", "hook_events", "hook_events_grouped")
_PAYLOAD_GROUPS = ("tool_call", "tool_name", "tool_args", "command", "path", "workspace", "common")


class VocabError(ValueError):
    pass


@dataclass(frozen=True)
class Vocab:
    version: str
    agent_tools: tuple[str, ...]
    sdk_tool_map: dict[str, str]
    default_agent_tools: tuple[str, ...]
    command_tools: tuple[str, ...]
    file_tools: tuple[str, ...]
    agent_models: tuple[str, ...]
    model_inherit: str
    exec_policies: tuple[str, ...]
    rule_triggers: tuple[str, ...]
    hook_events: tuple[str, ...]
    hook_events_grouped: tuple[str, ...]  # the events whose hooks.json entries are {matcher, hooks: [...]} groups
    payload_keys: dict[str, tuple[str, ...]]
    command_tool: str
    subagent_tool: str
    workflows_retired_on: date
    overrides: tuple[Path, ...] = ()  # override files that were applied
    override_label: str = ""
    problems: tuple[tuple[Path, str], ...] = field(default_factory=tuple)  # override files that were ignored, and why

    def describe(self) -> str:
        """``vocab v2026.10.1`` or ``vocab v2026.10.1 + override my-2026.11`` -- for messages."""
        extra = f" + override {self.override_label or 'file'}" if self.overrides else ""
        return f"vocab v{self.version}{extra}"

    def keys(self, group: str) -> tuple[str, ...]:
        return self.payload_keys[group]

    def model_tiers(self) -> tuple[str, ...]:
        """The model arrows that name a concrete tier (everything except 'inherit')."""
        return tuple(m for m in self.agent_models if m != self.model_inherit)


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VocabError(f"cannot read {path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise VocabError(f"{path.name} must be a JSON object")
    return data


def _strings(value: Any, what: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise VocabError(f"{what} must be a list of non-empty strings")
    return list(value)


def _edit_list(base: tuple[str, ...], spec: Any, what: str) -> tuple[str, ...]:
    if not isinstance(spec, dict) or set(spec) - {"add", "remove"}:
        raise VocabError(f"{what} must be an object with only 'add' and/or 'remove'")
    removed = set(_strings(spec.get("remove", []), f"{what}.remove"))
    out = [v for v in base if v not in removed]
    out += [v for v in _strings(spec.get("add", []), f"{what}.add") if v not in out]
    return tuple(out)


def _apply(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """``base`` with ``override`` merged in; raises :class:`VocabError` naming the first thing that is wrong."""
    out = dict(base)
    unknown = set(override) - {"vocab_version", "sdk_tool_map", "hook_payload_keys", "workflows_retired_on",
                               *_LIST_FIELDS}
    if unknown:
        raise VocabError(f"unknown key(s) {sorted(unknown)}")
    for name in _LIST_FIELDS:
        if name in override:
            out[name] = list(_edit_list(tuple(base[name]), override[name], name))
    if "sdk_tool_map" in override:
        spec = override["sdk_tool_map"]
        if not isinstance(spec, dict) or set(spec) - {"set", "remove"}:
            raise VocabError("sdk_tool_map must be an object with only 'set' and/or 'remove'")
        mapping = dict(base["sdk_tool_map"])
        for tool in _strings(spec.get("remove", []), "sdk_tool_map.remove"):
            mapping.pop(tool, None)
        added = spec.get("set", {})
        if not isinstance(added, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in added.items()):
            raise VocabError("sdk_tool_map.set must map tool names to SDK tool names (strings)")
        mapping.update(added)
        out["sdk_tool_map"] = mapping
    if "hook_payload_keys" in override:
        spec = override["hook_payload_keys"]
        if not isinstance(spec, dict) or set(spec) - set(_PAYLOAD_GROUPS):
            raise VocabError(f"hook_payload_keys groups must be among {list(_PAYLOAD_GROUPS)}")
        keys = {g: list(v) for g, v in base["hook_payload_keys"].items()}
        for group, group_spec in spec.items():
            keys[group] = list(_edit_list(tuple(keys[group]), group_spec, f"hook_payload_keys.{group}"))
        out["hook_payload_keys"] = keys
    if "workflows_retired_on" in override:
        try:
            date.fromisoformat(str(override["workflows_retired_on"]))
        except ValueError as exc:
            raise VocabError("workflows_retired_on must be an ISO date (YYYY-MM-DD)") from exc
        out["workflows_retired_on"] = str(override["workflows_retired_on"])
    if "vocab_version" in override and not isinstance(override["vocab_version"], str):
        raise VocabError("vocab_version must be a string")
    return out


def _from_data(data: dict[str, Any], *, overrides: tuple[Path, ...] = (), label: str = "",
               problems: tuple[tuple[Path, str], ...] = ()) -> Vocab:
    roles = data["roles"]
    return Vocab(
        version=data["vocab_version"],
        agent_tools=tuple(data["agent_tools"]),
        sdk_tool_map=dict(data["sdk_tool_map"]),
        default_agent_tools=tuple(data["default_agent_tools"]),
        command_tools=tuple(data["command_tools"]),
        file_tools=tuple(data["file_tools"]),
        agent_models=tuple(data["agent_models"]),
        model_inherit=data["model_inherit"],
        exec_policies=tuple(data["exec_policies"]),
        rule_triggers=tuple(data["rule_triggers"]),
        hook_events=tuple(data["hook_events"]),
        hook_events_grouped=tuple(data["hook_events_grouped"]),
        payload_keys={g: tuple(v) for g, v in data["hook_payload_keys"].items()},
        command_tool=roles["command_tool"],
        subagent_tool=roles["subagent_tool"],
        workflows_retired_on=date.fromisoformat(data["workflows_retired_on"]),
        overrides=overrides, override_label=label, problems=problems,
    )


@lru_cache(maxsize=1)
def _builtin_data() -> dict[str, Any]:
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def builtin() -> Vocab:
    """The vocabulary this agflow release was written against."""
    return _from_data(_builtin_data())


def override_files(workspace: Path | None = None, *, include_global: bool = True) -> list[Path]:
    files = [paths.global_state_dir() / OVERRIDE_NAME] if include_global else []
    if workspace is not None:
        files.append(paths.workspace_state_dir(workspace) / OVERRIDE_NAME)
    return files


def _signature(files: list[Path]) -> tuple[tuple[str, int, int], ...]:
    sig = []
    for f in files:
        try:
            st = f.stat()
            sig.append((str(f), st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append((str(f), -1, -1))
    return tuple(sig)


@lru_cache(maxsize=16)
def _build(signature: tuple[tuple[str, int, int], ...]) -> Vocab:
    data, applied, problems, label = _builtin_data(), [], [], ""
    for name, mtime, _size in signature:
        if mtime < 0:
            continue  # no override file: the normal case
        path = Path(name)
        try:
            raw = _read(path)
            data = _apply(data, raw)
        except VocabError as exc:
            problems.append((path, str(exc)))
            continue
        applied.append(path)
        label = str(raw.get("vocab_version") or label)
    return _from_data(data, overrides=tuple(applied), label=label, problems=tuple(problems))


def current(workspace: Path | None = None, *, include_global: bool = True) -> Vocab:
    """The built-in vocabulary with any valid override files applied (global first, then the workspace's).

    Cheap to call repeatedly: results are cached on the override files' modification times."""
    signature = _signature(override_files(workspace, include_global=include_global))
    if all(mtime < 0 for _name, mtime, _size in signature):  # no override file anywhere: the normal case
        return builtin()
    return _build(signature)
