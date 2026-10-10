"""Validators for every component type. `agflow doctor` is a thin wrapper around this module."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from agflow import frontmatter, paths
from agflow.inventory import Inventory
from agflow.models import Issue, Item, Kind, Level
from agflow.redact import SECRET_KEY_RE  # one definition of 'secret-looking'; also used for run artifacts

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SKILL_NAME_MAX = 64
SKILL_DESCRIPTION_MAX = 1024
SKILL_BODY_LINES_SOFT_MAX = 500
RULE_TRIGGERS = {"always_on", "model_decision", "glob", "manual"}
AGENT_MODELS = {"inherit", "flash", "pro"}
AGENT_EXEC_POLICIES = {"off", "auto", "eager", "sandbox"}
HOOK_EVENTS = {"PreToolUse", "PostToolUse", "PreInvocation", "PostInvocation", "Stop"}
ALWAYS_ON_BUDGET = 24_000  # chars of always-on context (GEMINI.md + always_on rules) before we warn

# Built-in Antigravity agent tools seen in 2.x docs/system prompts. Unknown names are only
# reported as INFO because MCP tools and new built-ins are legitimately allowed.
KNOWN_AGENT_TOOLS = {
    "list_dir", "view_file", "view_file_outline", "view_code_item", "grep_search", "find_by_name",
    "codebase_search", "write_to_file", "replace_file_content", "multi_replace_file_content",
    "run_command", "command_status", "send_command_input", "read_terminal", "browser_subagent",
    "read_url_content", "search_web", "generate_image", "invoke_subagent", "define_subagent",
    "ask_question", "notify_user", "task_boundary", "list_resources", "read_resource",
}
_LINK_RE = re.compile(r"\]\(([^)\s]+)\)")
_CODE_PATH_RE = re.compile(r"`((?:scripts|references|assets)/[^`\s]+)`")


def _err(code: str, msg: str, path: Path | None = None) -> Issue:
    return Issue(Level.ERROR, code, msg, path)


def _warn(code: str, msg: str, path: Path | None = None) -> Issue:
    return Issue(Level.WARNING, code, msg, path)


def _info(code: str, msg: str, path: Path | None = None) -> Issue:
    return Issue(Level.INFO, code, msg, path)


def _read_text(path: Path) -> tuple[str | None, list[Issue]]:
    """The one way validators read a file. A file that vanished between discovery and validation, that cannot be
    read, or that is not UTF-8 becomes an ``unreadable`` issue -- never an exception that aborts the whole doctor."""
    try:
        return path.read_text(encoding="utf-8"), []
    except (OSError, UnicodeDecodeError) as exc:
        return None, [_err("unreadable", str(exc), path)]


def _load_json(path: Path, code: str) -> tuple[Any, list[Issue]]:
    """Parse a JSON file; an empty (or whitespace-only) file counts as ``{}`` for every JSON validator alike."""
    text, issues = _read_text(path)
    if text is None:
        return None, issues
    try:
        return json.loads(text.strip() or "{}"), []
    except json.JSONDecodeError as exc:
        return None, [_err(code, f"invalid JSON: {exc}", path)]


def _load(path: Path, text: str | None = None) -> tuple[dict[str, Any] | None, str, list[Issue]]:
    if text is None:
        text, issues = _read_text(path)
        if text is None:
            return None, "", issues
    if not frontmatter.has_frontmatter(text):
        return None, text, [_err("frontmatter.missing", "file must start with YAML frontmatter (---)", path)]
    try:
        meta, body = frontmatter.parse(text)
    except frontmatter.FrontmatterError as exc:
        return None, text, [_err("frontmatter.invalid", str(exc), path)]
    return meta, body, []


# --- individual components ------------------------------------------------------------------


def validate_gemini_md(path: Path) -> list[Issue]:
    text, issues = _read_text(path)
    if text is None:
        return issues
    n = len(text)
    limit = paths.GEMINI_MD_CHAR_LIMIT
    if n > limit:
        return [_err("gemini_md.too_long",
                     f"{n} chars exceeds Antigravity's {limit}-char rules cap, so the end of it may not be loaded; "
                     f"trim at least {n - limit} chars — run `agflow gemini-md` to see where the size goes", path)]
    if n > limit * 0.9:
        return [_warn("gemini_md.near_limit", f"{n}/{limit} chars; little headroom left", path)]
    if not text.strip():
        return [_warn("gemini_md.empty", "GEMINI.md is empty", path)]
    return []


def validate_rule(path: Path) -> list[Issue]:
    text, issues = _read_text(path)
    if text is None:
        return issues
    meta, body, issues = _load(path, text)
    if meta is None:
        return issues
    trigger = meta.get("trigger")
    if trigger not in RULE_TRIGGERS:
        issues.append(_err("rule.trigger", f"trigger must be one of {sorted(RULE_TRIGGERS)}, got {trigger!r}", path))
    if trigger == "glob" and not meta.get("globs"):
        issues.append(_err("rule.globs", "trigger: glob requires a non-empty 'globs' value", path))
    if trigger == "model_decision" and not str(meta.get("description", "")).strip():
        issues.append(_warn("rule.description", "model_decision rules need a description to be picked", path))
    size = len(text)
    if size > paths.GEMINI_MD_CHAR_LIMIT:
        issues.append(_err("rule.too_long", f"{size} chars exceeds the {paths.GEMINI_MD_CHAR_LIMIT}-char cap", path))
    if not body.strip():
        issues.append(_warn("rule.empty", "rule body is empty", path))
    return issues


def validate_skill(path: Path) -> list[Issue]:
    meta, body, issues = _load(path)
    if meta is None:
        return issues
    folder = path.parent.name
    name = meta.get("name")
    desc = meta.get("description")
    if not name:
        issues.append(_err("skill.name.missing", "frontmatter 'name' is required", path))
    else:
        name = str(name)
        if not NAME_RE.match(name) or len(name) > SKILL_NAME_MAX:
            issues.append(_warn("skill.name.format",
                                f"name {name!r} should be lowercase-hyphenated, <= {SKILL_NAME_MAX} chars", path))
        if name != folder:
            # The Agent Skills spec wants name == folder, but Antigravity loads skills by `name` and
            # Google's own plugins use underscore folders with hyphenated names, so this is advisory.
            if name == folder.replace("_", "-"):
                issues.append(_info("skill.name.folder",
                                    f"folder {folder!r} uses underscores for skill {name!r} (works in Antigravity; "
                                    "the Agent Skills spec prefers identical names)", path))
            else:
                issues.append(_warn("skill.name.folder",
                                    f"skill {name!r} lives in folder {folder!r}; other tools may list it as "
                                    f"{folder!r} — rename one of them to match", path))
    if not desc or not str(desc).strip():
        issues.append(_err("skill.description.missing", "frontmatter 'description' is required", path))
    elif len(str(desc)) > SKILL_DESCRIPTION_MAX:
        issues.append(_warn("skill.description.long",
                            f"description exceeds {SKILL_DESCRIPTION_MAX} chars and may be truncated", path))
    elif len(str(desc)) < 40:
        issues.append(_warn("skill.description.short",
                            "description is too short to trigger reliably; say what it does AND when to use it",
                            path))
    if not body.strip():
        issues.append(_err("skill.body.empty", "SKILL.md body is empty", path))
    elif body.count("\n") > SKILL_BODY_LINES_SOFT_MAX:
        issues.append(_warn("skill.body.long",
                            f"body > {SKILL_BODY_LINES_SOFT_MAX} lines; move detail into references/", path))
    for ref in sorted(_local_refs(body)):
        if not _ref_exists(path.parent, ref):
            issues.append(_warn("skill.ref.missing", f"referenced file {ref!r} not found next to the skill", path))
    return issues


_REF_SUFFIXES = ("", ".md", ".py", ".txt", ".json", ".yaml", ".yml", ".sh", ".ps1")


def _ref_exists(skill_dir: Path, ref: str) -> bool:
    """A reference may be relative to the skill, a sibling skill, or the plugin/skills root,
    and is sometimes written without its extension."""
    bases = [skill_dir, skill_dir.parent, skill_dir.parent.parent]
    if skill_dir.parent.is_dir():
        bases += [d for d in skill_dir.parent.iterdir() if d.is_dir() and d != skill_dir]
    return any((base / f"{ref}{suffix}").exists() for base in bases for suffix in _REF_SUFFIXES)


def _local_refs(body: str) -> set[str]:
    refs = set(_CODE_PATH_RE.findall(body))
    for target in _LINK_RE.findall(body):
        if target.startswith(("http://", "https://", "#", "mailto:", "/")):
            continue
        refs.add(target.split("#", 1)[0])
    return {r for r in refs if r}


def validate_agent(path: Path, known_agents: set[str] | None = None) -> list[Issue]:
    meta, body, issues = _load(path)
    if meta is None:
        return issues
    name = meta.get("name")
    if not name:
        issues.append(_err("agent.name.missing", "frontmatter 'name' is required", path))
    elif not NAME_RE.match(str(name)):
        issues.append(_err("agent.name.format", f"name {name!r} must be lowercase-hyphenated", path))
    if not str(meta.get("description", "")).strip():
        issues.append(_err("agent.description.missing",
                           "frontmatter 'description' is required (the planner delegates on it)", path))
    model = meta.get("model")
    if model is not None and model not in AGENT_MODELS:
        issues.append(_err("agent.model", f"model must be one of {sorted(AGENT_MODELS)}, got {model!r}", path))
    policy = meta.get("commandExecutionPolicy")
    if policy is not None and policy not in AGENT_EXEC_POLICIES:
        issues.append(_err("agent.exec_policy",
                           f"commandExecutionPolicy must be one of {sorted(AGENT_EXEC_POLICIES)}, got {policy!r}",
                           path))
    for flag in ("mainAgent", "subagent", "excludeDefaultComponents"):
        if flag in meta and not isinstance(meta[flag], bool):
            issues.append(_err("agent.flag", f"{flag} must be true/false", path))
    if not meta.get("mainAgent") and not meta.get("subagent"):
        issues.append(_warn("agent.unreachable", "neither mainAgent nor subagent is true; agent can't be used", path))
    tools = meta.get("tools")
    if tools is not None:
        if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
            issues.append(_err("agent.tools", "tools must be a list of tool names", path))
        else:
            unknown = sorted(t for t in tools if t not in KNOWN_AGENT_TOOLS and not t.startswith("mcp"))
            if unknown:
                issues.append(_info("agent.tools.unknown", f"unrecognised tool names: {', '.join(unknown)}", path))
            if meta.get("agents") and "invoke_subagent" not in tools:
                issues.append(_warn("agent.tools.invoke",
                                    "declares 'agents' but lacks the invoke_subagent tool", path))
    if known_agents is not None:
        for dep in meta.get("agents") or []:
            dep_name = Path(str(dep)).stem if str(dep).endswith(".md") else str(dep)
            if dep_name not in known_agents:
                issues.append(_warn("agent.agents.unknown", f"declared subagent {dep!r} not found", path))
    if not body.strip():
        issues.append(_err("agent.body.empty", "agent system prompt (body) is empty", path))
    return issues


def validate_mcp_file(path: Path) -> list[Issue]:
    data, issues = _load_json(path, "mcp.json")
    if issues:
        return issues
    issues = []
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        return [_err("mcp.schema", "top-level 'mcpServers' object is required", path)]
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            issues.append(_err("mcp.server", f"server {name!r} must be an object", path))
            continue
        has_cmd = bool(cfg.get("command"))
        has_url = bool(cfg.get("serverUrl") or cfg.get("url") or cfg.get("httpUrl"))
        if has_cmd == has_url:
            issues.append(_err("mcp.transport",
                               f"server {name!r} needs exactly one of 'command' (stdio) or 'serverUrl' (http)", path))
        if "args" in cfg and not isinstance(cfg["args"], list):
            issues.append(_err("mcp.args", f"server {name!r}: 'args' must be a list", path))
        for section in ("env", "headers"):
            for key, value in (cfg.get(section) or {}).items():
                if SECRET_KEY_RE.search(key) and isinstance(value, str) and value and "${" not in value:
                    issues.append(_warn("mcp.secret",
                                        f"server {name!r}: {section}.{key} holds a literal secret; "
                                        "use a ${ENV_VAR} placeholder", path))
    return issues


def validate_hooks_file(path: Path) -> list[Issue]:
    data, issues = _load_json(path, "hooks.json")
    if issues:
        return issues
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        return [_err("hooks.schema", "top-level 'hooks' object is required", path)]
    issues: list[Issue] = []
    for event, entries in hooks.items():
        if event not in HOOK_EVENTS:
            issues.append(_err("hooks.event", f"unknown hook event {event!r}; valid: {sorted(HOOK_EVENTS)}", path))
        if not isinstance(entries, list):
            issues.append(_err("hooks.entries", f"{event}: expected a list of hook entries", path))
            continue
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict) or not str(entry.get("command", "")).strip():
                issues.append(_err("hooks.command", f"{event}[{i}]: 'command' is required", path))
            elif entry.get("matcher") is not None:
                try:
                    re.compile(str(entry["matcher"]))
                except re.error as exc:
                    issues.append(_err("hooks.matcher", f"{event}[{i}]: invalid matcher regex: {exc}", path))
    return issues


def validate_plugin_manifest(path: Path) -> list[Issue]:
    data, issues = _load_json(path, "plugin.json")
    if issues:
        return issues
    issues = []
    name = data.get("name") if isinstance(data, dict) else None
    if not name or not NAME_RE.match(str(name)):
        issues.append(_err("plugin.name", "plugin.json needs a lowercase-hyphenated 'name'", path))
    if not isinstance(data, dict) or not data.get("version"):
        issues.append(_info("plugin.version", "plugin.json has no 'version'", path))
    if not isinstance(data, dict) or not data.get("description"):
        issues.append(_info("plugin.description", "plugin.json has no 'description'", path))
    return issues


# --- whole inventory --------------------------------------------------------------------------


def validate_inventory(inv: Inventory) -> list[Issue]:
    issues: list[Issue] = [_err("load", msg, p) for p, msg in inv.load_errors]
    known_agents = inv.names(Kind.AGENT)
    known_skills = inv.names(Kind.SKILL)
    checked_files: set[Path] = set()

    def once(path: Path) -> bool:
        if path in checked_files:
            return False
        checked_files.add(path)
        return True

    always_on_chars: dict[str, int] = defaultdict(int)
    oversized_gemini_md: dict[str, int] = defaultdict(int)  # already reported as gemini_md.too_long
    for item in inv.items:
        if not item.enabled:
            continue
        if item.kind == Kind.GEMINI_MD:
            if item.meta.get("unreadable"):
                continue  # discovery already reported it as a `load` error
            found = validate_gemini_md(item.path)
            issues += found
            always_on_chars[_scope_group(item)] += item.meta.get("chars", 0)
            if any(i.code == "gemini_md.too_long" for i in found):
                oversized_gemini_md[_scope_group(item)] += item.meta.get("chars", 0)
        elif item.kind == Kind.RULE:
            issues += validate_rule(item.path)
            if item.meta.get("trigger") == "always_on":
                text, read_issues = _read_text(item.path)
                issues += read_issues
                always_on_chars[_scope_group(item)] += len(text or "")
        elif item.kind == Kind.SKILL:
            issues += validate_skill(item.path)
        elif item.kind == Kind.AGENT:
            issues += validate_agent(item.path, known_agents)
        elif item.kind == Kind.MCP and once(item.path):
            issues += validate_mcp_file(item.path)
        elif item.kind == Kind.HOOK and once(item.path):
            issues += validate_hooks_file(item.path)
        elif item.kind == Kind.PLUGIN:
            issues += validate_plugin_manifest(item.path)
        elif item.kind == Kind.WORKFLOW:
            issues.append(_warn("workflow.deprecated",
                                f"workflow '{item.name}' — Antigravity retires workflows on 2026-11-01; "
                                "run `agflow migrate` to convert it to a skill", item.path))
        elif item.kind == Kind.GRAPH:
            issues += _validate_graph_item(item, known_agents, known_skills)

    for group, chars in always_on_chars.items():
        # Don't repeat an oversized GEMINI.md (already an error): only warn when the other always-on
        # sources are too big on their own.
        if chars - oversized_gemini_md[group] > ALWAYS_ON_BUDGET or (not oversized_gemini_md[group]
                                                                      and chars > ALWAYS_ON_BUDGET):
            issues.append(_warn("context.always_on",
                                f"{group}: {chars} chars of always-on rules; prefer model_decision/glob triggers"))

    issues += _shadowing(inv)
    issues += _stale_disabled(inv)
    issues += _interrupted_installs(inv)
    if inv.workspace is not None:
        issues += _run_store_issues(inv.workspace)
    for lay in inv.layouts:
        if lay.scope.value == "legacy":
            leftovers = legacy_config(lay)
            if leftovers:
                issues.append(_warn("legacy.config",
                                    f"Antigravity 1.x configuration not used by 2.x: {'; '.join(leftovers)} — "
                                    "run `agflow migrate --dry-run` to preview moving it", lay.root))
    issues = _dedupe(issues)
    _attribute_sources(inv, issues)
    return issues


def legacy_config(lay: Any) -> list[str]:
    """What a 1.x layout still holds that 2.x ignores. `~/.gemini/antigravity` itself is still
    Antigravity's app-data directory, so its mere existence is not a problem."""
    out = []
    if lay.skills_dir and lay.skills_dir.is_dir():
        n = sum(1 for d in lay.skills_dir.iterdir() if (d / "SKILL.md").is_file())
        if n:
            out.append(f"{n} skill(s) in {lay.skills_dir}")
    if lay.rules_dir and lay.rules_dir.is_dir():
        n = sum(1 for f in lay.rules_dir.glob("*.md"))
        if n:
            out.append(f"{n} rule(s) in {lay.rules_dir}")
    for wdir in lay.workflows_dirs:
        n = sum(1 for f in wdir.glob("*.md")) if wdir.is_dir() else 0
        if n:
            out.append(f"{n} workflow(s) in {wdir}")
    if lay.mcp_file and lay.mcp_file.is_file():
        try:
            legacy = set(json.loads(lay.mcp_file.read_text(encoding="utf-8") or "{}").get("mcpServers") or {})
        except (ValueError, AttributeError, OSError):
            legacy = set()
        current_file = paths.global_layout().mcp_file
        current: set[str] = set()
        if current_file and current_file.is_file():
            try:
                current = set(json.loads(current_file.read_text(encoding="utf-8") or "{}").get("mcpServers") or {})
            except (ValueError, AttributeError, OSError):
                current = set()
        missing = sorted(legacy - current)
        if missing:
            out.append(f"MCP server(s) {', '.join(missing)} only in {lay.mcp_file}")
    return out


def _attribute_sources(inv: Inventory, issues: list[Issue]) -> None:
    by_item = {i.path: i.scope for i in inv.items}
    roots = sorted(inv.sources, key=lambda s: len(str(s[0])), reverse=True)
    for issue in issues:
        if issue.source:
            continue
        if issue.path is None:
            issue.source = "global" if issue.message.startswith("global") else "workspace"
            continue
        if issue.path in by_item:
            issue.source = by_item[issue.path]
            continue
        for root, label in roots:
            if issue.path == root or root in issue.path.parents:
                issue.source = label
                break
        else:
            issue.source = "other"


def by_source(issues: list[Issue]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for i in issues:
        counts = out.setdefault(i.source or "other", {lvl.value: 0 for lvl in Level})
        counts[i.level.value] += 1
    return out


def _scope_group(item: Item) -> str:
    return "workspace+plugins" if item.scope != "global" else "global"


def _installed_twice(inv: Inventory) -> list[Issue]:
    """One warning per plugin that is installed in more than one location."""
    by_name: dict[str, list[Item]] = defaultdict(list)
    for item in inv.of(Kind.PLUGIN):
        if paths.plugin_base_name(item.scope) is not None:
            by_name[item.name].append(item)
    issues = []
    for name, items in by_name.items():
        if len(items) < 2:
            continue
        where = "; ".join(f"{i.scope} ({i.path.parent})" for i in items)
        issues.append(_warn("plugin.installed_twice",
                            f"plugin '{name}' is installed {len(items)}x: {where}. Antigravity loads every copy, so "
                            f"each of its skills and agents exists {len(items)}x and '{items[0].scope}' takes "
                            "precedence. Keep one copy (run `agflow fix`).", items[0].path))
    return issues


def _shadowing(inv: Inventory) -> list[Issue]:
    issues = _installed_twice(inv)
    for kind in (Kind.SKILL, Kind.AGENT):
        by_name: dict[str, list[Item]] = defaultdict(list)
        for item in inv.of(kind):
            by_name[item.name].append(item)
        for name, items in by_name.items():
            scopes = [i.scope for i in items]
            bases = {paths.plugin_base_name(s) for s in scopes}
            if len(items) > 1 and len(set(scopes)) == len(items) and len(bases) == 1 and None not in bases:
                continue  # the same plugin installed in several places: reported once as plugin.installed_twice
            if len(items) > 1 and len(set(scopes)) > 1:
                issues.append(_info(f"{kind.value}.shadowed",
                                    f"{kind.value} '{name}' defined in {', '.join(scopes)}; "
                                    f"'{scopes[0]}' wins", items[0].path))
            elif len(items) > 1:
                issues.append(_err(f"{kind.value}.duplicate",
                                   f"{kind.value} '{name}' defined {len(items)}x in {scopes[0]}", items[0].path))
    return issues


def _stale_disabled(inv: Inventory) -> list[Issue]:
    """Disabled-items index entries with no parked copy: a harmless leftover, or a parked item that was deleted."""
    issues = []
    for index_file, entry, original_in_place in inv.stale_disabled:
        what = f"{entry.get('kind')} '{entry.get('name')}'"
        if original_in_place:
            issues.append(_info("disabled.stale",
                                f"{what} has an entry in the disabled-items index but was never moved (a disable was "
                                "interrupted); it is still in place and enabled. The entry is removed the next time "
                                "you enable or disable it", index_file))
        else:
            issues.append(_warn("disabled.lost",
                                f"{what} is listed as disabled but neither its parked copy nor its original location "
                                "exists any more; remove the entry from the index file if you deleted it on purpose",
                                index_file))
    return issues


def _interrupted_installs(inv: Inventory) -> list[Issue]:
    """Leftovers of a crashed install/upgrade. Until they are recovered, the previous plugin may be out of place."""
    state_dirs = [paths.workspace_state_dir(inv.workspace)] if inv.workspace is not None else []
    if any(lay.scope == paths.Scope.GLOBAL for lay in inv.layouts):
        state_dirs.append(paths.global_state_dir())
    issues = []
    for state_dir in state_dirs:
        tmp_root = paths.install_tmp_dir(state_dir)
        leftovers = sorted(p.name for p in tmp_root.iterdir()) if tmp_root.is_dir() else []
        if leftovers:
            issues.append(_warn("install.interrupted",
                                f"an agflow install or upgrade was interrupted ({len(leftovers)} leftover item(s) in "
                                f"{tmp_root}). Run `agflow install` to finish it or `agflow uninstall` to roll it "
                                "back; either first puts your previous plugin back if it was moved aside", tmp_root))
    return issues


def _run_store_issues(workspace: Path) -> list[Issue]:
    """Runs that listings and hooks skip, and a run index that had to be rebuilt -- both are otherwise silent."""
    from agflow.graph.store import INDEX_FILE, RunStore

    store = RunStore(workspace)
    if not store.root.is_dir():
        return []
    health = store.health()
    issues = [_warn("runs.corrupt",
                    f"run '{run_id}': its state.json is unreadable or invalid, so run listings and hooks skip it; "
                    "restore the file or delete the run folder",
                    store.run_dir(run_id) / "state.json") for run_id in health["corrupt_runs"]]
    if health["index_damaged"]:
        aside = store.root / (INDEX_FILE + ".corrupt")
        issues.append(_warn("runs.index.corrupt",
                            "the run index was damaged; agflow rebuilt it from the runs on disk. The damaged copy "
                            "is kept for inspection; delete it once you have looked at it", aside))
    return issues


def _validate_graph_item(item: Item, known_agents: set[str], known_skills: set[str]) -> list[Issue]:
    from agflow.graph.schema import GraphError, load_graph  # local import: graph depends on validate

    try:
        graph = load_graph(item.path)
    except GraphError as exc:
        return [_err("graph.invalid", str(exc), item.path)]
    issues = []
    for node in graph.nodes:
        if node.agent and node.agent not in known_agents:
            issues.append(_warn("graph.agent.unknown", f"node '{node.id}' uses unknown agent '{node.agent}'",
                                item.path))
        if node.skill and node.skill not in known_skills:
            issues.append(_warn("graph.skill.unknown", f"node '{node.id}' uses unknown skill '{node.skill}'",
                                item.path))
    return issues


def _dedupe(issues: list[Issue]) -> list[Issue]:
    seen = set()
    out = []
    for i in issues:
        key = (i.level, i.code, i.message, str(i.path))
        if key not in seen:
            seen.add(key)
            out.append(i)
    return out


def summarize(issues: list[Issue]) -> dict[str, int]:
    counts = {lvl.value: 0 for lvl in Level}
    for i in issues:
        counts[i.level.value] += 1
    return counts
