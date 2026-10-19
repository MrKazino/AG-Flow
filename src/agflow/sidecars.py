"""AG-Flow's two Antigravity sidecars, and what can be said about them without touching the user's settings.

* ``agflow/manager``     - the **AG-Flow Manager** panel: a UI sidecar that opens in Antigravity's side pane
  (``agflow dashboard --sidecar``);
* ``agflow/maintenance`` - a scheduled sidecar that runs ``agflow maintenance run`` once a day (compatibility and update
  checks; it never installs anything).

Antigravity keeps sidecars **off** until the user turns them on (``config.json``, ``sidecars`` map, keyed by
``<plugin>/<sidecar>``), and its own guidance tells agents never to enable one. So this module only *reads*: whether
the files are installed, whether they are valid, whether the user has enabled them, and the exact link and steps to
show. Nothing here writes to ``config.json``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agflow import paths

PLUGIN = paths.PLUGIN_NAME
MANAGER = f"{PLUGIN}/manager"
MAINTENANCE = f"{PLUGIN}/maintenance"
LABELS = {MANAGER: "AG-Flow Manager", MAINTENANCE: "AG-Flow daily check"}
PILL = f"[AG-Flow Manager](sidecar://{MANAGER}/)"
RESTART_POLICIES = ("always", "on-failure", "never")
AUX_PANE = "SIDECAR_UI_ENTRYPOINT_AUX_PANE"


def pill_link() -> str:
    """The markdown link that opens the manager in Antigravity's side pane (only shown once the panel is enabled)."""
    return PILL


def check_sidecar_json(data: Any, *, ui: bool) -> list[str]:
    """Problems in a ``sidecar.json`` against what Antigravity documents (empty list: it looks right)."""
    if not isinstance(data, dict):
        return ["sidecar.json must contain a JSON object"]
    problems = []
    if bool(data.get("command")) == bool(data.get("builtin")):
        problems.append("exactly one of `command` or `builtin` must be set")
    if "args" in data and not (isinstance(data["args"], list) and all(isinstance(a, str) for a in data["args"])):
        problems.append("`args` must be a list of strings")
    policy = data.get("restart_policy")
    if policy is not None and policy not in RESTART_POLICIES:
        problems.append(f"`restart_policy` must be one of {', '.join(RESTART_POLICIES)}")
    if data.get("builtin") == "schedule":
        args = data.get("args") or []
        if not args or len(str(args[0]).split("CRON_TZ=")[-1].split()) < 5:
            problems.append("`schedule` needs a five-field cron expression as its first argument")
    if ui:
        views = (data.get("ui_config") or {}).get("views") if isinstance(data.get("ui_config"), dict) else None
        if data.get("has_web_ui") is not True:
            problems.append("a UI sidecar needs `has_web_ui: true`")
        if not (isinstance(views, list) and any(isinstance(v, dict) and v.get("entrypoint") == AUX_PANE
                                                and isinstance(v.get("path"), str) for v in views)):
            problems.append(f"a UI sidecar needs a `ui_config.views` entry with entrypoint {AUX_PANE}")
    return problems


@dataclass
class SidecarStatus:
    id: str
    label: str
    installed: bool = False
    valid: bool = False
    enabled: bool | None = None     # None: the user has not decided (Antigravity treats that as off)
    folder: str = ""
    problems: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.installed and self.valid and self.enabled is True

    def to_json(self) -> dict[str, Any]:
        return {**self.__dict__, "ready": self.ready}


def enabled_in_config(sidecar_id: str, config_file: Path | None = None) -> bool | None:
    """The user's choice for ``sidecar_id`` in Antigravity's ``config.json`` (None: no entry, or unreadable)."""
    try:
        data = json.loads((config_file or paths.antigravity_config_file()).read_text(encoding="utf-8"))
        entry = data["sidecars"][sidecar_id]["enabled"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return entry if isinstance(entry, bool) else None


def status(plugin_dir: Path | None = None, config_file: Path | None = None) -> list[SidecarStatus]:
    """Where each AG-Flow sidecar stands. ``plugin_dir``: the installed AG-Flow plugin (default: the global install)."""
    from agflow import install as inst

    root = plugin_dir if plugin_dir is not None else inst.plugin_target("global", None)
    out = []
    for sidecar_id in (MANAGER, MAINTENANCE):
        item = SidecarStatus(sidecar_id, LABELS[sidecar_id], enabled=enabled_in_config(sidecar_id, config_file))
        folder = paths.plugin_sidecars_dir(root) / sidecar_id.split("/", 1)[1]
        item.folder = str(folder)
        spec = folder / "sidecar.json"
        if spec.is_file():
            item.installed = True
            try:
                data = json.loads(spec.read_text(encoding="utf-8"))
                item.problems = check_sidecar_json(data, ui=sidecar_id == MANAGER)
            except (OSError, ValueError):
                item.problems = ["sidecar.json cannot be read"]
            item.valid = not item.problems
        out.append(item)
    return out


def enable_steps(sidecar_id: str) -> list[str]:
    """What the *user* does to turn a sidecar on. AG-Flow does not do it for them."""
    return [
        "Open Antigravity's Customizations (plugins) page, select AG-Flow and switch its sidecar on "
        "(the Automations dashboard lists scheduled ones),",
        f'or add this to ~/.gemini/config/config.json under "sidecars": "{sidecar_id}": {{ "enabled": true }}',
        "then reload the Antigravity window.",
    ]


def lines(items: list[SidecarStatus]) -> list[str]:
    out = []
    for item in items:
        state = ("ready" if item.ready else "not installed" if not item.installed else
                 "installed but invalid" if not item.valid else "installed, switched off")
        out.append(f"{item.label} ({item.id}): {state}")
        out += [f"    problem: {p}" for p in item.problems]
        if item.installed and item.valid and item.enabled is not True:
            out += [f"    {step}" for step in enable_steps(item.id)]
    if any(i.id == MANAGER and i.ready for i in items):
        out.append(f"Open the manager from a chat with this link: {PILL}")
    return out
