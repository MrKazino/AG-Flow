"""`agflow fix`: turn doctor issues into previewable, reversible repairs.

Principles
* Preview first — every fix lists exactly what it will change.
* Two tiers — ``safe`` fixes are mechanical and additive; ``review`` fixes rename, move or remove things
  and are only applied when chosen explicitly (with an option where more than one outcome is valid).
* Reversible — everything a batch touches is backed up first and recorded in fix-history.json;
  :func:`undo_last` restores it byte-for-byte and deletes files the batch created.
* Ownership — files of third-party plugins are never edited (their publisher manages them); those issues
  get manual guidance instead.
"""

from __future__ import annotations

import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agflow import frontmatter, paths, platform_vocab
from agflow.fsutil import read_json, unique_sibling, write_json
from agflow.inventory import Inventory
from agflow.models import Issue, Item, Kind, Level
from agflow.validate import NAME_RE, summarize, validate_inventory

HISTORY_MAX = 20
GEMINI_MD_TARGET = paths.GEMINI_MD_CHAR_LIMIT - 1_000  # leave headroom after moving sections out


class FixError(RuntimeError):
    pass


# --- transaction: backups + history --------------------------------------------------------------------


def _history_file() -> Path:
    return paths.global_state_dir() / "fix-history.json"


class Txn:
    """Records the prior state of every path before it is changed."""

    def __init__(self) -> None:
        self.id = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
        self.root = paths.backups_dir() / f"{self.id}-fix"
        self.ops: list[dict[str, Any]] = []
        self._seen: set[str] = set()

    def touch(self, path: Path) -> None:
        key = str(path.absolute())
        if key in self._seen:
            return
        self._seen.add(key)
        if path.exists() or path.is_symlink():
            dest = self.root / f"{len(self.ops):04d}-{path.name or 'root'}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            if path.is_dir() and not path.is_symlink():
                shutil.copytree(path, dest, symlinks=True)
            else:
                shutil.copy2(path, dest, follow_symlinks=False)
            self.ops.append({"path": key, "existed": True, "backup": str(dest), "dir": path.is_dir()})
        else:
            # record the outermost directory this batch creates, so undo leaves no empty folders behind
            top = path.absolute()
            while not top.parent.exists() and top.parent != top.parent.parent:
                top = top.parent
            if str(top) != key and str(top) in self._seen:
                return
            self._seen.add(str(top))
            self.ops.append({"path": str(top), "existed": False, "backup": None, "dir": False})

    def write(self, path: Path, text: str) -> None:
        """Write text, keeping the file's existing line endings (CRLF on Windows) and UTF-8 BOM."""
        crlf, bom = False, False
        if path.is_file():
            raw = path.read_bytes()
            crlf, bom = b"\r\n" in raw, raw.startswith(b"\xef\xbb\xbf")
        self.touch(path)
        data = text.replace("\r\n", "\n")
        if crlf:
            data = data.replace("\n", "\r\n")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.agflow-tmp")
        tmp.write_bytes((b"\xef\xbb\xbf" if bom else b"") + data.encode("utf-8"))
        tmp.replace(path)

    def remove(self, path: Path) -> None:
        self.touch(path)
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()

    def move(self, src: Path, dst: Path) -> None:
        if dst.exists():
            raise FixError(f"{dst} already exists")
        self.touch(src)
        self.touch(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))

    def commit(self, workspace: Path | None, titles: list[str]) -> None:
        if not self.ops:
            return
        history = read_json(_history_file(), []) or []
        history.append({"id": self.id, "time": time.time(), "workspace": str(workspace) if workspace else None,
                        "fixes": titles, "backup_dir": str(self.root), "ops": self.ops})
        write_json(_history_file(), history[-HISTORY_MAX:])


def history() -> list[dict[str, Any]]:
    return list(reversed(read_json(_history_file(), []) or []))


def undo_last() -> dict[str, Any]:
    """Restore everything the most recent fix batch changed."""
    items = read_json(_history_file(), []) or []
    if not items:
        raise FixError("there is no fix to undo")
    batch = items.pop()
    restored, removed = [], []
    for op in reversed(batch["ops"]):
        path = Path(op["path"])
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()
        if op["existed"]:
            src = Path(op["backup"])
            path.parent.mkdir(parents=True, exist_ok=True)
            if op.get("dir"):
                shutil.copytree(src, path, symlinks=True)
            else:
                shutil.copy2(src, path, follow_symlinks=False)
            restored.append(str(path))
        else:
            removed.append(str(path))
    write_json(_history_file(), items)
    return {"id": batch["id"], "fixes": batch["fixes"], "restored": restored, "removed": removed}


# --- plan model ------------------------------------------------------------------------------------------


@dataclass
class Option:
    id: str
    label: str
    changes: list[str]
    apply: Callable[[Txn], None]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "changes": self.changes}


@dataclass
class Fix:
    id: str
    code: str
    level: str
    title: str
    path: str | None
    source: str
    tier: str  # "safe" | "review"
    options: list[Option]
    note: str = ""

    @property
    def default(self) -> str:
        return self.options[0].id

    def option(self, option_id: str | None) -> Option:
        for opt in self.options:
            if opt.id == (option_id or self.default):
                return opt
        raise FixError(f"fix {self.id} has no option '{option_id}' (choose: {', '.join(o.id for o in self.options)})")

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "code": self.code, "level": self.level, "title": self.title, "path": self.path,
                "source": self.source, "tier": self.tier, "default": self.default, "note": self.note,
                "options": [o.to_dict() for o in self.options]}


@dataclass
class Manual:
    id: str
    code: str
    level: str
    title: str
    path: str | None
    source: str
    steps: list[str]

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class FixPlan:
    workspace: Path | None
    issues: list[Issue]
    fixes: list[Fix] = field(default_factory=list)
    manual: list[Manual] = field(default_factory=list)
    probed: bool = False  # the plan's issues include the result of actually running agflow's hook commands

    def get(self, fix_id: str) -> Fix | None:
        return next((f for f in self.fixes if f.id == fix_id), None)

    def to_dict(self) -> dict[str, Any]:
        return {"workspace": str(self.workspace) if self.workspace else None,
                "counts": summarize(self.issues),
                "fixes": [f.to_dict() for f in self.fixes],
                "manual": [m.to_dict() for m in self.manual],
                "fixable_issue_ids": [f.id for f in self.fixes]}


# --- helpers -----------------------------------------------------------------------------------------------


def _slug(text: str, fallback: str = "item") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:64].rstrip("-")
    return s or fallback


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def derive_description(body: str, name: str) -> str:
    """A description that says what + when, built from the file's first heading and sentence."""
    heading, sentence = "", ""
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("<!--", "```", "---")):
            continue
        if stripped.startswith("#"):
            heading = heading or stripped.lstrip("#").strip()
            continue
        sentence = re.split(r"(?<=[.!?])\s", stripped.lstrip("-*> ").strip(), maxsplit=1)[0]
        break
    topic = heading or name.replace("-", " ").replace("_", " ")
    what = sentence.rstrip(".") if sentence else f"{topic} guidance"
    text = f"{what}. Use when the task involves {topic.lower()}."
    if len(text) < 40:
        text = f"{what}. Use this whenever the current task involves {topic.lower()} in any way."
    return text[:1024]


def _fm_text(meta: dict[str, Any]) -> str:
    return ", ".join(f"{k}: {str(v).lower() if isinstance(v, bool) else v}" for k, v in meta.items())


def _set_frontmatter(txn: Txn, path: Path, changes: dict[str, Any]) -> None:
    text = _read(path)
    txn.write(path, frontmatter.update(text, changes) if frontmatter.has_frontmatter(text)
              else frontmatter.dump(changes, text))


def _meta(path: Path) -> tuple[dict[str, Any], str]:
    try:
        return frontmatter.parse(_read(path))
    except frontmatter.FrontmatterError:
        return {}, _read(path)


def _user_owned(issue: Issue) -> bool:
    return issue.source in ("global", "workspace")


# Warnings about agflow's own hooks.json that a reinstall repairs: it rewrites the file in the documented layout and
# points its commands at the agflow that is running (so a dead executable path is replaced by a working one).
HOOK_CODES_FIXED_BY_REINSTALL = ("hooks.inert", "hooks.unguarded", "hooks.broken")


def _is_agflow(issue: Issue) -> bool:
    return paths.plugin_base_name(issue.source) == paths.PLUGIN_NAME


def _item_at(inv: Inventory, path: Path | None) -> Item | None:
    return next((i for i in inv.items if path is not None and i.path == path), None)


# --- fix builders (one per issue code) ------------------------------------------------------------------------


def _fix(issue: Issue, title: str, tier: str, options: list[Option], note: str = "") -> Fix:
    return Fix(issue.id, issue.code, issue.level.value, title, str(issue.path) if issue.path else None,
               issue.source, tier, options, note)


def _frontmatter_value_fix(issue: Issue, title: str, key: str, choices: list[tuple[str, str, Any]],
                           tier: str) -> Fix:
    path = issue.path
    assert path is not None
    options = [Option(oid, label, [f"{path}: set `{key}: {value}` in the frontmatter"],
                      lambda txn, v=value: _set_frontmatter(txn, path, {key: v})) for oid, label, value in choices]
    return _fix(issue, title, tier, options)


def _build(issue: Issue, inv: Inventory) -> Fix | Manual | None:
    code, path = issue.code, issue.path
    vocab = platform_vocab.current(inv.workspace)
    if issue.level == Level.INFO:
        return None
    if code == "plugin.installed_twice":
        return _fix_installed_twice(issue, inv)
    if code == "gemini_md.too_long" and path is not None and path == paths.global_layout().gemini_md:
        return _fix_gemini_md(issue, inv)
    if code == "legacy.config":
        return _fix_legacy(issue, inv)
    if _is_agflow(issue) and (issue.level == Level.ERROR or code in HOOK_CODES_FIXED_BY_REINSTALL):
        return _fix_reinstall_agflow(issue, inv)
    if not _user_owned(issue) or path is None:
        return _manual(issue)
    item = _item_at(inv, path)

    if code == "skill.name.folder" and item is not None:
        return _fix_skill_folder(issue, item)
    if code == "skill.name.missing":
        name = path.parent.name if NAME_RE.match(path.parent.name) else _slug(path.parent.name, "skill")
        return _frontmatter_value_fix(issue, f"Set the skill name to '{name}'", "name",
                                      [("set", f"name: {name}", name)], "safe")
    if code == "agent.name.missing":
        stem = path.parent.name if path.name == "agent.md" else path.stem
        return _frontmatter_value_fix(issue, f"Set the agent name to '{_slug(stem)}'", "name",
                                      [("set", f"name: {_slug(stem)}", _slug(stem))], "safe")
    if code in ("skill.description.missing", "agent.description.missing", "rule.description"):
        meta, body = _meta(path)
        desc = derive_description(body, str(meta.get("name") or path.parent.name))
        return _frontmatter_value_fix(issue, "Add a description (what it does + when to use it)", "description",
                                      [("derive", f"description: {desc}", desc)],
                                      "safe" if code == "rule.description" else "review")
    if code == "frontmatter.missing" and item is not None:
        return _fix_missing_frontmatter(issue, item)
    if code == "rule.trigger":
        meta, _ = _meta(path)
        current = str(meta.get("trigger", "")).lower().replace("-", "_").replace(" ", "_")
        normalized = {"alwayson": "always_on", "always": "always_on", "modeldecision": "model_decision",
                      "model": "model_decision", "auto": "model_decision"}.get(current.replace("_", ""), current)
        order = ["model_decision", "always_on", "manual"]
        if normalized in vocab.rule_triggers and normalized != "glob":
            order.remove(normalized)
            order.insert(0, normalized)
        return _frontmatter_value_fix(
            issue, "Set a valid rule trigger", "trigger",
            [(t, {"model_decision": "model_decision — loaded when relevant",
                  "always_on": "always_on — in every prompt (costs context)",
                  "manual": "manual — only when you @-mention it"}[t], t) for t in order], "review")
    if code == "rule.globs":
        return _frontmatter_value_fix(issue, "Glob rule without globs: switch it to model_decision", "trigger",
                                      [("model_decision", "trigger: model_decision", "model_decision")], "review")
    if code == "agent.model":
        return _frontmatter_value_fix(issue, "Use a valid model tier", "model",
                                      [(m, f"model: {m}", m) for m in vocab.agent_models], "review")
    if code == "agent.exec_policy":
        order = ["auto"] + sorted(set(vocab.exec_policies) - {"auto"})
        return _frontmatter_value_fix(issue, "Use a valid commandExecutionPolicy", "commandExecutionPolicy",
                                      [(p, f"commandExecutionPolicy: {p}", p) for p in order], "review")
    if code == "agent.flag":
        return _fix_agent_flags(issue)
    if code == "workflow.deprecated":
        return _fix_workflow(issue, inv)
    return _manual(issue)


def _fix_installed_twice(issue: Issue, inv: Inventory) -> Fix | Manual:
    from agflow import install as inst

    first = _item_at(inv, issue.path)
    plugin = first.name if first is not None else ""
    copies = [i for i in inv.of(Kind.PLUGIN, enabled=None)
              if i.name == plugin and paths.plugin_base_name(i.scope) is not None]
    if plugin != paths.PLUGIN_NAME or len(copies) < 2:
        return _manual(issue)
    rank = {"global": 0, "workspace": 1, "agy-cli": 2}

    def location(item: Item) -> str:
        return paths.plugin_location(item.path.parent, inv.workspace)

    def remove_copy(txn: Txn, item: Item) -> None:
        where = location(item)
        ws = inv.workspace if where == "workspace" else None
        manifest = inst.manifest_path(where, ws) if where in ("global", "workspace") else None
        data = read_json(manifest, None) if manifest is not None else None
        if not data or Path(inst.plugin_target(where, ws)).absolute() != item.path.parent.absolute():
            txn.remove(item.path.parent)  # not installed by agflow's installer: just remove the folder
            return
        # agflow's own uninstall also restores the files the install replaced; back up all it can touch
        for p in [*data.get("created", []), *data.get("modified", {}), *data.get("managed_blocks", [])]:
            txn.touch(Path(p))
        txn.touch(item.path.parent)
        txn.touch(manifest)
        inst.uninstall(where, ws)

    def keep_only(keep: Item) -> Callable[[Txn], None]:
        def run(txn: Txn) -> None:
            for other in copies:
                if other is not keep:
                    remove_copy(txn, other)
        return run

    options = []
    for keep in sorted(copies, key=lambda i: rank[location(i)]):
        options.append(Option(
            f"keep-{location(keep)}", f"Keep the {location(keep)} copy ({keep.path.parent})",
            [f"remove the {location(o)} copy {o.path.parent} (files agflow's install replaced are restored)"
             for o in copies if o is not keep],
            keep_only(keep)))
    note = ("The global copy serves every workspace and is the one easy-installer.bat updates; keep it unless you "
            "want agflow in this workspace only.")
    return _fix(issue, f"agflow is installed {len(copies)}x: keep one copy", "review", options, note)


def _fix_gemini_md(issue: Issue, inv: Inventory) -> Fix | Manual:
    from agflow import install as inst
    from agflow.gemini_md import segments

    path = issue.path
    assert path is not None
    text = _read(path)
    changes: list[str] = []
    agflow_block = inst.BLOCK_RE.search(text)
    working = inst.BLOCK_RE.sub("", text) if agflow_block else text
    global_plugin = paths.global_layout().plugins_dir / paths.PLUGIN_NAME  # type: ignore[operator]
    if agflow_block:
        changes.append(f"remove agflow's own block ({len(agflow_block.group(0)):,} chars); agflow keeps working "
                       "through its always-on plugin rule")
    segs = segments(working)
    movable = sorted([s for s in segs if s["heading"] and s["block"] is None],
                     key=lambda s: s["end"] - s["start"], reverse=True)
    skills_dir = paths.global_layout().skills_dir
    assert skills_dir is not None
    used = {p.name for p in skills_dir.iterdir()} if skills_dir.is_dir() else set()
    chosen, size = [], len(working)
    pointer_len = 160
    for seg in movable:
        if size + pointer_len <= GEMINI_MD_TARGET:
            break
        name = f"gemini-md-{_slug(seg['title'], 'section')}"[:64].rstrip("-")
        base, n = name, 2
        while name in used:
            name = f"{base}-{n}"[:64]
            n += 1
        used.add(name)
        chosen.append((seg, name))
        size -= seg["end"] - seg["start"]
    final = size + (pointer_len if chosen else 0)
    for seg, name in chosen:
        changes.append(f"move section '{seg['title']}' ({seg['end'] - seg['start']:,} chars) to on-demand skill "
                       f"{skills_dir / name / 'SKILL.md'}")
    if not chosen and not agflow_block:
        return _manual(issue, extra=["No movable sections were found (the size is in blocks managed by other tools). "
                                     "Shorten those blocks in the tool that writes them."])
    blocked = sorted({s["block"] for s in segs if s["block"]})
    note = f"GEMINI.md goes from {len(text):,} to about {final:,} chars."
    if final > paths.GEMINI_MD_CHAR_LIMIT:
        note += (f" That is still over {paths.GEMINI_MD_CHAR_LIMIT:,}: the rest is in blocks managed by "
                 f"{', '.join(blocked)} — shorten them in that tool.")
    changes.append(f"{path}: keep the rest in place and add one line naming the new skills")

    def apply(txn: Txn) -> None:
        current = _read(path)
        if current != text:
            raise FixError("GEMINI.md changed since the fix was planned; run the fix again")
        if agflow_block and (global_plugin / "plugin.json").is_file():
            rule = paths.plugin_layout(global_plugin).rules_dir / inst.MASTER_RULE
            if not rule.exists():
                bundled = paths.plugin_layout(paths.bundled_plugin_dir()).gemini_md
                master = bundled.read_text(encoding="utf-8").strip() + "\n"
                txn.write(rule, inst.master_rule(master))
        stamp = time.strftime("%Y-%m-%d")
        for seg, name in chosen:
            section = working[seg["start"]:seg["end"]].strip() + "\n"
            body = (f"<!-- moved from ~/.gemini/GEMINI.md by `agflow fix` on {stamp}; `agflow fix --undo` "
                    "puts it back -->\n\n" + section)
            desc = (f"Personal rules about {seg['title']} (moved from GEMINI.md so they load only when relevant). "
                    f"Use whenever the task involves {seg['title'].lower()}.")[:1024]
            txn.write(skills_dir / name / "SKILL.md", frontmatter.dump({"name": name, "description": desc}, body))
        keep = working
        for seg, _ in sorted(chosen, key=lambda c: c[0]["start"], reverse=True):
            keep = keep[:seg["start"]] + keep[seg["end"]:]
        if chosen:
            keep = keep.rstrip() + ("\n\nSituational rules moved to on-demand skills (loaded when relevant): "
                                    + ", ".join(name for _, name in chosen) + ".\n")
        txn.write(path, keep)

    return _fix(issue, "Shrink GEMINI.md under the 12,000-character limit", "review",
                [Option("move-sections", "Move the largest sections into on-demand skills", changes, apply)], note)


def _fix_reinstall_agflow(issue: Issue, inv: Inventory) -> Fix:
    from agflow import install as inst

    plugin_dir = next((i.path.parent for i in inv.of(Kind.PLUGIN)
                       if i.scope == issue.source and i.path is not None), None)
    where = paths.plugin_location(plugin_dir, inv.workspace) if plugin_dir else "global"
    scope = "workspace" if where == "workspace" else "global"

    def apply(txn: Txn) -> None:
        target = inst.plugin_target(scope, inv.workspace)  # type: ignore[arg-type]
        txn.touch(target)
        txn.touch(inst.manifest_path(scope, inv.workspace))  # type: ignore[arg-type]
        gm = paths.global_layout().gemini_md
        if gm is not None:
            txn.touch(gm)
        inst.install(scope, inv.workspace, allow_duplicate=True)  # type: ignore[arg-type]

    return _fix(issue, f"Reinstall agflow's {scope} copy (restores its original files)", "safe",
                [Option("reinstall", "Reinstall", [f"reinstall agflow into {plugin_dir}"], apply)])


def _fix_skill_folder(issue: Issue, item: Item) -> Fix | Manual:
    meta, _ = _meta(item.path)
    name, folder_dir = str(meta.get("name", "")), item.path.parent
    options = []
    target = folder_dir.parent / name
    if NAME_RE.match(name) and not target.exists():
        options.append(Option("rename-folder", f"Rename the folder to '{name}' (keeps /{name})",
                              [f"rename {folder_dir} -> {target}"],
                              lambda txn: txn.move(folder_dir, target)))
    if NAME_RE.match(folder_dir.name):
        options.append(Option("set-name", f"Change the skill name to '{folder_dir.name}' (it becomes "
                                          f"/{folder_dir.name})",
                              [f"{item.path}: set `name: {folder_dir.name}`"],
                              lambda txn: _set_frontmatter(txn, item.path, {"name": folder_dir.name})))
    if not options:
        return _manual(issue)
    return _fix(issue, "Make the skill name and its folder match", "review", options)


def _fix_missing_frontmatter(issue: Issue, item: Item) -> Fix | Manual:
    path = item.path
    body = _read(path)
    if item.kind == Kind.SKILL:
        name = path.parent.name if NAME_RE.match(path.parent.name) else _slug(path.parent.name, "skill")
        meta = {"name": name, "description": derive_description(body, name)}
        return _fix(issue, "Add the required skill frontmatter", "review",
                    [Option("add", "Add name + description", [f"{path}: add frontmatter ({_fm_text(meta)})"],
                            lambda txn: txn.write(path, frontmatter.dump(meta, body)))])
    if item.kind == Kind.AGENT:
        name = _slug(path.parent.name if path.name == "agent.md" else path.stem, "agent")
        meta = {"name": name, "description": derive_description(body, name), "subagent": True}
        return _fix(issue, "Add the required agent frontmatter", "review",
                    [Option("add", "Add name, description, subagent: true",
                            [f"{path}: add frontmatter ({_fm_text(meta)})"],
                            lambda txn: txn.write(path, frontmatter.dump(meta, body)))])
    if item.kind == Kind.RULE:
        desc = derive_description(body, path.stem)
        options = []
        for trig, label in (("model_decision", "model_decision — loaded when relevant"),
                            ("always_on", "always_on — in every prompt (costs context)")):
            meta = {"trigger": trig, "description": desc}
            options.append(Option(trig, label, [f"{path}: add frontmatter ({_fm_text(meta)})"],
                                  lambda txn, m=meta: txn.write(path, frontmatter.dump(m, body))))
        return _fix(issue, "Add the trigger frontmatter Antigravity needs to load this rule", "review", options)
    return _manual(issue)


def _fix_agent_flags(issue: Issue) -> Fix | Manual:
    path = issue.path
    assert path is not None
    meta, _ = _meta(path)
    changes: dict[str, bool] = {}
    for flag in ("mainAgent", "subagent", "excludeDefaultComponents"):
        value = meta.get(flag)
        if flag in meta and not isinstance(value, bool):
            text = str(value).strip().lower()
            if text in ("true", "yes", "1", "on"):
                changes[flag] = True
            elif text in ("false", "no", "0", "off", ""):
                changes[flag] = False
            else:
                return _manual(issue)
    return _fix(issue, "Turn text flags into real true/false values", "safe",
                [Option("coerce", ", ".join(f"{k}: {str(v).lower()}" for k, v in changes.items()),
                        [f"{path}: set {k}: {str(v).lower()}" for k, v in changes.items()],
                        lambda txn: _set_frontmatter(txn, path, changes))])


def _fix_workflow(issue: Issue, inv: Inventory) -> Fix | Manual:
    from agflow.migrate import slugify, workflow_to_skill

    wf = issue.path
    assert wf is not None
    ws_root = paths.workspace_root_dir(inv.workspace) if inv.workspace else None
    in_ws = ws_root is not None and ws_root in wf.parents
    skills_dir = paths.workspace_layout(inv.workspace).skills_dir if in_ws else paths.global_layout().skills_dir
    assert skills_dir is not None
    name = slugify(wf.stem)
    target = skills_dir / name / "SKILL.md"
    if target.exists():
        return _manual(issue, extra=[f"A skill named '{name}' already exists ({target}); rename the workflow file "
                                     "and run `agflow fix` again."])

    bak = unique_sibling(wf)  # the same naming rule as `agflow migrate`; never onto an earlier backup

    def apply(txn: Txn) -> None:
        txn.write(target, workflow_to_skill(_read(wf), name))
        txn.move(wf, bak)

    return _fix(issue, f"Convert workflow '{wf.stem}' into the skill /{name}", "safe",
                [Option("convert", "Convert to a skill", [f"create {target}", f"rename {wf} -> {bak.name}"],
                        apply)])


def _fix_legacy(issue: Issue, inv: Inventory) -> Fix:
    from agflow.migrate import migrate_legacy_global, migrate_legacy_workspace

    legacy_root = issue.path
    is_global = legacy_root is not None and legacy_root == paths.legacy_global_layout().root
    report = (migrate_legacy_global(dry_run=True) if is_global
              else migrate_legacy_workspace(inv.workspace, dry_run=True))  # type: ignore[arg-type]

    def apply(txn: Txn) -> None:
        if is_global:
            g = paths.global_layout()
            for p in (g.skills_dir, g.mcp_file):
                txn.touch(p)  # type: ignore[arg-type]
            migrate_legacy_global()
        else:
            old, new = paths.legacy_workspace_layout(inv.workspace), paths.workspace_layout(inv.workspace)  # type: ignore[arg-type]
            for p in (old.rules_dir, old.skills_dir, new.rules_dir, new.skills_dir):
                txn.touch(p)  # type: ignore[arg-type]
            migrate_legacy_workspace(inv.workspace)  # type: ignore[arg-type]

    changes = report.actions + [f"skip (already exists): {c}" for c in report.conflicts]
    return _fix(issue, "Move Antigravity 1.x configuration to the 2.x locations", "review",
                [Option("migrate", "Migrate", changes or ["nothing left to migrate"], apply)])


# --- manual guidance --------------------------------------------------------------------------------------------


def _find_line(path: Path | None, needle: str) -> int | None:
    if path is None or not path.is_file() or not needle:
        return None
    for n, line in enumerate(_read(path).splitlines(), start=1):
        if needle in line:
            return n
    return None


def _manual(issue: Issue, extra: list[str] | None = None) -> Manual:
    code, path, src = issue.code, issue.path, issue.source
    steps: list[str] = []
    base = paths.plugin_base_name(src)
    if base is not None and base != paths.PLUGIN_NAME and code != "plugin.installed_twice":
        steps.append(f"This file belongs to the '{base}' plugin; its publisher manages it and any edit would be "
                     "overwritten by its next update, so agflow doesn't change it.")
        steps.append("Nothing is needed on your side; if it matters to you, report it to the plugin's publisher: "
                     f"{issue.message}.")
        return Manual(issue.id, code, issue.level.value, issue.message, str(path) if path else None, src, steps)
    ref = re.search(r"referenced file '([^']+)'", issue.message)
    if code == "skill.ref.missing" and ref:
        line = _find_line(path, ref.group(1))
        steps.append(f"Create {path.parent / ref.group(1) if path else ref.group(1)}, or remove the reference"
                     + (f" on line {line}" if line else "") + f" in {path}.")
    elif code == "skill.body.long":
        steps.append("Move long, rarely needed sections into references/<topic>.md next to SKILL.md and link them "
                     "from the body; keep SKILL.md focused on the procedure.")
    elif code == "gemini_md.too_long":
        steps.append("Run `agflow gemini-md` to see which sections and tool-written blocks take the space.")
    elif code == "context.always_on":
        steps.append("Change situational rules from `trigger: always_on` to `model_decision` (with a precise "
                     "description) or `glob`, so they load only when relevant.")
    elif code == "mcp.secret":
        key = re.search(r"\.(\w+) holds", issue.message)
        var = key.group(1) if key else "THE_VARIABLE"
        steps.append(f"Store the value as an environment variable (Windows: `setx {var} \"...\"`; macOS/Linux: add "
                     f"`export {var}=...` to your shell profile), then replace the value in {path} with "
                     f"`${{{var}}}` and restart Antigravity.")
    elif code.endswith(".duplicate"):
        steps.append("Two items in the same place use the same name; rename one of them (its `name:` and folder).")
    elif code in ("mcp.json", "hooks.json", "plugin.json"):
        steps.append(f"Fix the JSON syntax in {path}: {issue.message}")
    elif code == "plugin.installed_twice":
        steps.append("Remove all but one copy of the plugin: delete (or uninstall through the tool that installed "
                     "it) the copy in the location you don't need.")
    elif code == "install.interrupted":
        steps.append("Run `agflow install` to finish the interrupted install (it first puts a plugin that was moved "
                     "aside back), or `agflow uninstall` to roll it back. Don't delete the leftover folder by hand: "
                     "it may hold your previous plugin.")
    elif code == "graph.invalid":
        steps.append(f"Edit the graph YAML ({path}) — {issue.message}. Check it with `agflow graph validate`.")
    if extra:
        steps.extend(extra)
    if not steps:
        steps.append(f"Edit {path}: {issue.message}" if path else issue.message)
    return Manual(issue.id, code, issue.level.value, issue.message, str(path) if path else None, src, steps)


# --- public API -------------------------------------------------------------------------------------------------


def migrate_undoable(workspace: Path | None, *, include_global: bool = True) -> Any:
    """`agflow migrate`, recorded as one batch in the same history as `agflow fix`, so that `agflow fix --undo`
    puts back what it moved, renamed, copied or merged (N3: it used to have no way back). Returns the report."""
    from agflow.migrate import migrate_all

    txn = Txn()
    for path in migrate_all(workspace, include_global=include_global, dry_run=True).touched:
        txn.touch(path)
    try:
        return migrate_all(workspace, include_global=include_global)
    finally:  # a migration that stopped half-way is recorded too: its finished part can be undone
        txn.commit(workspace, ["agflow migrate"])


def build_plan(workspace: Path | None, *, probe_hooks: bool = False) -> FixPlan:
    inv = Inventory.discover(workspace)
    issues = validate_inventory(inv, probe_hooks=probe_hooks)
    plan = FixPlan(workspace=workspace, issues=issues, probed=probe_hooks)
    for issue in issues:
        built = _build(issue, inv)
        if isinstance(built, Fix):
            plan.fixes.append(built)
        elif isinstance(built, Manual):
            plan.manual.append(built)
    tier_order = {"safe": 0, "review": 1}
    plan.fixes.sort(key=lambda f: (tier_order[f.tier], f.code == "plugin.installed_twice", f.level != "error"))
    return plan


@dataclass
class ApplyResult:
    applied: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    unresolved: list[dict[str, Any]] = field(default_factory=list)
    before: dict[str, int] = field(default_factory=dict)
    after: dict[str, int] = field(default_factory=dict)
    batch: str | None = None
    backup_dir: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def select(plan: FixPlan, *, include_review: bool = False, only: list[str] | None = None,
           options: dict[str, str] | None = None) -> dict[str, str | None]:
    """Selections for apply(): safe fixes by default, review fixes (default option) if requested."""
    chosen: dict[str, str | None] = {}
    for fix in plan.fixes:
        if only and fix.id not in only:
            continue
        if only or fix.tier == "safe" or include_review:
            chosen[fix.id] = (options or {}).get(fix.id)
    return chosen


def apply(workspace: Path | None, selections: dict[str, str | None], *, plan: FixPlan | None = None) -> ApplyResult:
    """Apply the chosen fixes as one undoable batch.

    A caller that has just built (and shown) the plan passes it in to avoid discovering the whole setup a second
    time; without one, a fresh plan is built so ids that stopped applying meanwhile are reported as skipped. The
    re-check after the changes always looks at the new state, so it is never skipped.
    """
    plan = plan if plan is not None else build_plan(workspace)
    result = ApplyResult(before=summarize(plan.issues))
    txn = Txn()
    titles = []
    order = {f.id: n for n, f in enumerate(plan.fixes)}
    for fix_id in sorted(selections, key=lambda i: order.get(i, 10_000)):
        fix = plan.get(fix_id)
        if fix is None:
            result.skipped.append({"id": fix_id, "reason": "no longer applicable (already fixed or changed)"})
            continue
        try:
            option = fix.option(selections[fix_id])
            option.apply(txn)
        except Exception as exc:  # report and continue; everything done so far is undoable
            result.failed.append({"id": fix.id, "title": fix.title, "error": f"{type(exc).__name__}: {exc}"})
            continue
        result.applied.append({"id": fix.id, "title": fix.title, "option": option.label, "changes": option.changes})
        titles.append(f"{fix.title} [{option.id}]")
    txn.commit(workspace, titles)
    if txn.ops:
        result.batch, result.backup_dir = txn.id, str(txn.root)
    after = validate_inventory(Inventory.discover(workspace), probe_hooks=plan.probed)
    result.after = summarize(after)
    remaining = {i.id for i in after}
    result.unresolved = [a for a in result.applied if a["id"] in remaining]
    return result


__all__ = ["ApplyResult", "Fix", "FixError", "FixPlan", "Manual", "apply", "build_plan", "history", "migrate_undoable",
           "select", "undo_last"]
