"""Validators for every component type. `agflow doctor` is a thin wrapper around this module."""

from __future__ import annotations

import itertools
import json
import os
import re
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from agflow import frontmatter, hookfile, paths, platform_vocab
from agflow.inventory import Inventory
from agflow.models import Issue, Item, Kind, Level
from agflow.redact import SECRET_KEY_RE  # one definition of 'secret-looking'; also used for run artifacts

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SKILL_NAME_MAX = 64
SKILL_DESCRIPTION_MAX = 1024
SKILL_BODY_LINES_SOFT_MAX = 500
_BUILTIN = platform_vocab.builtin()
# Module-level names kept for compatibility. The platform vocabulary itself lives in platform_vocab (one table, one
# version); the validators below use `platform_vocab.current()` so that a user's override applies.
RULE_TRIGGERS = set(_BUILTIN.rule_triggers)
AGENT_MODELS = set(_BUILTIN.agent_models)
AGENT_EXEC_POLICIES = set(_BUILTIN.exec_policies)
HOOK_EVENTS = set(_BUILTIN.hook_events)
ALWAYS_ON_BUDGET = 24_000  # chars of always-on context (GEMINI.md + always_on rules) before we warn

KNOWN_AGENT_TOOLS = set(_BUILTIN.agent_tools)  # unknown names are only INFO: MCP tools / new built-ins are fine
_LINK_RE = re.compile(r"\]\(([^)\s]+)\)")
_CODE_PATH_RE = re.compile(r"`((?:scripts|references|assets)/[^`\s]+)`")


def _vocab_hint(vocab: platform_vocab.Vocab) -> str:
    """Appended to errors about Antigravity-defined names: which table was used and how to extend it."""
    return (f" ({vocab.describe()}; if Antigravity added this, extend the table with "
            f"{paths.global_state_dir() / platform_vocab.OVERRIDE_NAME})")


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


def validate_rule(path: Path, vocab: platform_vocab.Vocab | None = None) -> list[Issue]:
    vocab = vocab or platform_vocab.current()
    text, issues = _read_text(path)
    if text is None:
        return issues
    meta, body, issues = _load(path, text)
    if meta is None:
        return issues
    trigger = meta.get("trigger")
    if trigger not in vocab.rule_triggers:
        issues.append(_err("rule.trigger", f"trigger must be one of {sorted(vocab.rule_triggers)}, got {trigger!r}"
                           f"{_vocab_hint(vocab)}", path))
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


def validate_skill(path: Path, *, cache: _FsCache | None = None) -> list[Issue]:
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
    cache = cache or _FsCache()
    for ref in sorted(_local_refs(body)):
        if not _ref_exists(path.parent, ref, cache):
            issues.append(_warn("skill.ref.missing", f"referenced file {ref!r} not found next to the skill", path))
    return issues


_REF_SUFFIXES = ("", ".md", ".py", ".txt", ".json", ".yaml", ".yml", ".sh", ".ps1")


_EXISTS_MEMO_MAX = 50_000  # bounds the memory a pathological tree (everything broken) can cost


class _FsCache:
    """What one validation pass has already asked the filesystem: sibling-skill listings and existence answers.

    A pass is short and the tree does not change under it, so remembering is safe; nothing outlives the pass.
    Paths are kept as strings here: this is the hot loop of the reference check, and building a ``Path`` per candidate
    cost more than the system calls it replaced.
    """

    def __init__(self) -> None:
        self._listings: dict[Path, list[Path]] = {}
        self._siblings: dict[Path, list[str]] = {}
        self._exists: dict[str, bool] = {}

    def subdirs(self, directory: Path) -> list[Path]:
        """The directories directly inside ``directory`` (symlinks to directories included), listed once."""
        found = self._listings.get(directory)
        if found is None:
            try:
                with os.scandir(directory) as entries:
                    found = [directory / e.name for e in entries if e.is_dir()]
            except OSError:
                found = []
            self._listings[directory] = found
        return found

    def siblings(self, skill_dir: Path) -> list[str]:
        """The other skill directories in the same skills root."""
        found = self._siblings.get(skill_dir)
        if found is None:
            found = self._siblings[skill_dir] = [str(d) for d in self.subdirs(skill_dir.parent) if d != skill_dir]
        return found

    def exists(self, path: str) -> bool:
        found = self._exists.get(path)
        if found is None:
            found = os.path.exists(path)
            if len(self._exists) < _EXISTS_MEMO_MAX:
                self._exists[path] = found
        return found


def _ref_exists(skill_dir: Path, ref: str, cache: _FsCache | None = None) -> bool:
    """A reference may be relative to the skill, a sibling skill, or the plugin/skills root,
    and is sometimes written without its extension.

    The nearest places are tried first and the sibling skills (listed once per skills root, not once per reference)
    only when those miss. For a base directory that lacks the reference's first path component none of its candidates
    can exist, so they are not probed one by one.
    """
    cache = cache or _FsCache()
    where = Path(ref)
    parts = where.parts
    # `..` is resolved by the OS differently on Windows (lexically) and POSIX (through the directory), so such a
    # reference is always probed in full
    prunable = len(parts) > 1 and not where.anchor and ".." not in parts
    near = (str(skill_dir), str(skill_dir.parent), str(skill_dir.parent.parent))
    for base in itertools.chain(near, cache.siblings(skill_dir)):
        if prunable and not cache.exists(os.path.join(base, parts[0])):
            continue
        if any(cache.exists(os.path.join(base, f"{ref}{suffix}")) for suffix in _REF_SUFFIXES):
            return True
    return False


_URL_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")  # https://, sidecar:// ... (not a Windows "C:\\" path)


def _local_refs(body: str) -> set[str]:
    refs = set(_CODE_PATH_RE.findall(body))
    for target in _LINK_RE.findall(body):
        if target.startswith(("#", "mailto:", "/")) or _URL_SCHEME_RE.match(target):
            continue
        refs.add(target.split("#", 1)[0])
    return {r for r in refs if r}


def validate_agent(path: Path, known_agents: set[str] | None = None,
                   vocab: platform_vocab.Vocab | None = None) -> list[Issue]:
    vocab = vocab or platform_vocab.current()
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
    if model is not None and model not in vocab.agent_models:
        issues.append(_err("agent.model", f"model must be one of {sorted(vocab.agent_models)}, got {model!r}"
                           f"{_vocab_hint(vocab)}", path))
    policy = meta.get("commandExecutionPolicy")
    if policy is not None and policy not in vocab.exec_policies:
        issues.append(_err("agent.exec_policy",
                           f"commandExecutionPolicy must be one of {sorted(vocab.exec_policies)}, got {policy!r}"
                           f"{_vocab_hint(vocab)}", path))
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
            unknown = sorted(t for t in tools if t not in vocab.agent_tools and not t.startswith("mcp"))
            if unknown:
                issues.append(_info("agent.tools.unknown",
                                    f"unknown to this agflow version ({vocab.describe()}): {', '.join(unknown)}; "
                                    "MCP tools and newer built-ins are fine", path))
            if meta.get("agents") and vocab.subagent_tool not in tools:
                issues.append(_warn("agent.tools.invoke",
                                    f"declares 'agents' but lacks the {vocab.subagent_tool} tool", path))
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


def _check_hook_matcher(where: str, matcher: Any, path: Path) -> list[Issue]:
    if matcher is None:
        return []
    if not isinstance(matcher, str):
        return [_err("hooks.matcher", f"{where}: 'matcher' must be a string (a regex on the tool name)", path)]
    try:
        re.compile(matcher)
    except re.error as exc:
        return [_err("hooks.matcher", f"{where}: invalid matcher regex: {exc}", path)]
    return []


def _check_hook_handler(where: str, handler: Any, path: Path) -> list[Issue]:
    if not isinstance(handler, dict) or not str(handler.get("command", "")).strip():
        return [_err("hooks.command", f"{where}: 'command' is required", path)]
    issues: list[Issue] = []
    if handler.get("type", "command") != "command":
        issues.append(_warn("hooks.type", f"{where}: only \"type\": \"command\" is supported by Antigravity", path))
    timeout = handler.get("timeout")
    if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0):
        issues.append(_warn("hooks.timeout", f"{where}: 'timeout' must be a positive number of seconds", path))
    return issues


def validate_hooks_file(path: Path, vocab: platform_vocab.Vocab | None = None) -> list[Issue]:
    """Antigravity's documented layout (see ``hookfile``): ``{hook name: {event: [groups or handlers]}}``."""
    vocab = vocab or platform_vocab.current()
    data, issues = _load_json(path, "hooks.json")
    if issues:
        return issues
    if not isinstance(data, dict):
        return [_err("hooks.schema", "hooks.json must be a JSON object that maps hook names to their events", path)]
    issues = []
    for hook, spec in data.items():
        if not isinstance(spec, dict):
            issues.append(_err("hooks.schema", f"hook {hook!r}: expected an object that maps events to handlers", path))
            continue
        if "enabled" in spec and not isinstance(spec["enabled"], bool):
            issues.append(_err("hooks.schema", f"hook {hook!r}: 'enabled' must be true or false", path))
        for event, entries in spec.items():
            if event == "enabled":
                continue
            if event not in vocab.hook_events:
                issues.append(_err("hooks.event", f"unknown hook event {event!r}; valid: {sorted(vocab.hook_events)}"
                                   f"{_vocab_hint(vocab)}", path))
            if not isinstance(entries, list):
                issues.append(_err("hooks.entries", f"{event}: expected a list of hook entries", path))
                continue
            for i, entry in enumerate(entries):
                where = f"{event}[{i}]"
                members = entry.get("hooks") if isinstance(entry, dict) else None
                if isinstance(members, list):  # a group: {matcher, hooks: [...]}
                    issues += _check_hook_matcher(where, entry.get("matcher"), path)
                    for j, handler in enumerate(members):
                        issues += _check_hook_handler(f"{where}.hooks[{j}]", handler, path)
                else:  # a plain handler
                    issues += _check_hook_handler(where, entry, path)
                    if isinstance(entry, dict):
                        issues += _check_hook_matcher(where, entry.get("matcher"), path)
    inert = [r for r in hookfile.handlers(data, vocab) if r.inert and r.enabled]
    if inert:
        events = ", ".join(sorted({r.event for r in inert}))
        issues.append(_warn("hooks.inert",
                            f"{len(inert)} hook entr{'y' if len(inert) == 1 else 'ies'} ({events}) are written in a "
                            "layout Antigravity ignores, so they never run: tool events need "
                            "{\"matcher\": ..., \"hooks\": [{\"command\": ...}]} groups, the other events plain "
                            "handlers (agflow before 0.3 wrote the wrong layout, which left its safety guard and run "
                            "ledger switched off). `agflow install` rewrites agflow's own file; see "
                            "Antigravity's hooks documentation for other files", path))
    return issues


def _unguarded_hook_issues(inv: Inventory) -> list[Issue]:
    """agflow's own hook commands that would fail the agent's turn if agflow itself broke (``hookfile.fail_open``)."""
    issues: list[Issue] = []
    seen: set[Path] = set()
    for item in inv.of(Kind.HOOK):
        if item.path in seen or not item.enabled:
            continue
        seen.add(item.path)
        data, problems = _load_json(item.path, "hooks.json")
        if problems:
            continue
        bare = [r for r in hookfile.handlers(data) if r.enabled and not r.inert
                and hookfile.is_agflow_command(r.handler.get("command"))
                and hookfile.FAILOPEN_MARKER not in str(r.handler.get("command"))]
        if bare:
            events = ", ".join(sorted({r.event for r in bare}))
            issues.append(_warn("hooks.unguarded",
                                f"{len(bare)} agflow hook command(s) ({events}) are not wrapped to fail open: if "
                                "agflow's executable breaks (a deleted or upgraded tool environment), Antigravity "
                                "reports \"hook failed\" and ends the agent's turn. `agflow install` rewrites them",
                                item.path))
    return issues


# What Antigravity reads from plugin.json (its plugin skill and docs). Any other top-level field is dropped silently.
MANIFEST_FIELDS = ("name", "displayName", "description", "logo", "suggestedPrompts", "version", "disabled")
MANIFEST_PROMPTS_MAX = 3


def validate_plugin_manifest(path: Path) -> list[Issue]:
    data, issues = _load_json(path, "plugin.json")
    if issues:
        return issues
    issues = []
    if isinstance(data, dict):
        extra = sorted(k for k in data if k not in MANIFEST_FIELDS)
        if extra:
            issues.append(_info("plugin.manifest.field",
                                f"plugin.json has field(s) Antigravity ignores: {', '.join(extra)}. It reads only "
                                f"{', '.join(MANIFEST_FIELDS)}; the others are dropped without a message", path))
        prompts = data.get("suggestedPrompts")
        if prompts is not None and not (isinstance(prompts, list) and all(isinstance(p, str) for p in prompts)):
            issues.append(_warn("plugin.manifest.prompts", "suggestedPrompts must be a list of strings", path))
        elif isinstance(prompts, list) and len(prompts) > MANIFEST_PROMPTS_MAX:
            issues.append(_info("plugin.manifest.prompts",
                                f"suggestedPrompts lists {len(prompts)}; Antigravity shows the first "
                                f"{MANIFEST_PROMPTS_MAX} and ignores the rest", path))
    name = data.get("name") if isinstance(data, dict) else None
    if not name or not NAME_RE.match(str(name)):
        issues.append(_err("plugin.name", "plugin.json needs a lowercase-hyphenated 'name'", path))
    if not isinstance(data, dict) or not data.get("version"):
        issues.append(_info("plugin.version", "plugin.json has no 'version'", path))
    if not isinstance(data, dict) or not data.get("description"):
        issues.append(_info("plugin.description", "plugin.json has no 'description'", path))
    return issues


# --- whole inventory --------------------------------------------------------------------------


def _health_issues() -> list[Issue]:
    """What the daily check last found about Antigravity and updates (cached, offline, never raises)."""
    try:
        from agflow import maintenance

        health = maintenance.health()
    except Exception:  # noqa: BLE001 - a cache read must never break the doctor
        return []
    out = []
    status = health["compat"]["status"]
    if status == "breaking":
        out.append(Issue(Level.WARNING, "compat.breaking", "Antigravity no longer documents something AG-Flow depends "
                         "on; run `agflow sync check` for what and `agflow update check`", None, "agflow"))
    elif status == "drift":
        out.append(Issue(Level.INFO, "compat.drift", "Antigravity changed since AG-Flow was verified against it; run "
                         "`agflow sync check`, then `agflow sync apply`", None, "agflow"))
    if health["update"]["available"]:
        out.append(Issue(Level.INFO, "update.available", f"AG-Flow {health['update']['latest']} is available; run "
                         "`agflow update plan`", None, "agflow"))
    return out


def _checks_an_install(inv: Inventory) -> bool:
    """False for `doctor --path <plugin>`: that looks at a plugin template, not at what is installed here."""
    return any(lay.scope in (paths.Scope.GLOBAL, paths.Scope.WORKSPACE) for lay in inv.layouts)


def validate_inventory(inv: Inventory, *, probe_hooks: bool = False) -> list[Issue]:
    """Every problem in the inventory. ``probe_hooks`` also *runs* agflow's installed hook commands (a few hundred ms
    each) to prove they start -- for `agflow doctor` / `agflow fix`; the dashboard and repeated callers skip it."""
    issues: list[Issue] = [_err("load", msg, p) for p, msg in inv.load_errors]
    # `doctor --path <plugin>` has no global layout: the user's own override must not leak into that check
    vocab = platform_vocab.current(inv.workspace, include_global=any(lay.scope == paths.Scope.GLOBAL
                                                                      for lay in inv.layouts))
    issues += [_warn("vocab.override.invalid", f"ignored this vocabulary override: {msg}", p)
               for p, msg in vocab.problems]
    issues += [_info("vocab.override", f"{vocab.describe()} (applied from {p})", p) for p in vocab.overrides]
    known_agents = inv.names(Kind.AGENT)
    known_skills = inv.names(Kind.SKILL)
    checked_files: set[Path] = set()

    def once(path: Path) -> bool:
        if path in checked_files:
            return False
        checked_files.add(path)
        return True

    fs_cache = _FsCache()
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
            issues += validate_rule(item.path, vocab)
            if item.meta.get("trigger") == "always_on":
                always_on_chars[_scope_group(item)] += item.meta.get("chars", 0)  # measured when it was discovered
        elif item.kind == Kind.SKILL:
            issues += validate_skill(item.path, cache=fs_cache)
        elif item.kind == Kind.AGENT:
            issues += validate_agent(item.path, known_agents, vocab)
        elif item.kind == Kind.MCP and once(item.path):
            issues += validate_mcp_file(item.path)
        elif item.kind == Kind.HOOK and once(item.path):
            issues += validate_hooks_file(item.path, vocab)
        elif item.kind == Kind.PLUGIN:
            issues += validate_plugin_manifest(item.path)
        elif item.kind == Kind.WORKFLOW:
            retired = vocab.workflows_retired_on
            verb = "retires" if retired > date.today() else "retired"
            issues.append(_warn("workflow.deprecated",
                                f"workflow '{item.name}' — Antigravity {verb} workflows on {retired.isoformat()}; "
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
    issues += _parking_issues(inv)
    issues += _hook_health_issues(vocab, inv)
    if _checks_an_install(inv):
        issues += _health_issues()
        issues += _unguarded_hook_issues(inv)
        if probe_hooks:
            from agflow import hook_probe

            issues += hook_probe.probe_issues(inv, vocab=vocab)
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


def _hook_health_issues(vocab: platform_vocab.Vocab, inv: Inventory) -> list[Issue]:
    """The safety hooks got something they could not understand: the guard is not protecting those calls."""
    from agflow import hook_health

    if not any(lay.scope == paths.Scope.GLOBAL for lay in inv.layouts):
        return []  # `doctor --path <plugin>` is about the plugin, not about this machine's hook traffic
    summary = hook_health.summary()
    if summary is None:
        return []
    when = lambda ts: time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))  # noqa: E731
    kinds = ", ".join(f"{k} x{n}" for k, n in sorted(summary["counts"].items()))
    keys = ", ".join(summary["last_keys"][:12]) or "(none)"
    return [_warn("hooks.guard_blind",
                  f"agflow's safety hooks could not understand {summary['total']} call(s) from Antigravity in the "
                  f"last {summary['window_days']} days ({kinds}; first {when(summary['first_seen'])}, last "
                  f"{when(summary['last_seen'])} in {summary['last_event']}). While this lasts the guard is NOT "
                  "protecting those calls - it lets the agent run. Antigravity may have changed its hook payload. "
                  f"Payload keys of the last call: {keys}. To fix it without a release: run Antigravity with "
                  f"AGFLOW_HOOK_DEBUG=1, read one payload in hook-debug.jsonl, and add the new key names to "
                  f"hook_payload_keys in {paths.global_state_dir() / platform_vocab.OVERRIDE_NAME} "
                  f"({vocab.describe()}). Delete {hook_health.health_file()} once resolved.",
                  hook_health.health_file())]


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


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


def _parking_issues(inv: Inventory) -> list[Issue]:
    """The second defence of enable/disable: a "disabled" item Antigravity still sees, and a parking area it can find.

    A disabled item is *parked* where Antigravity does not look; nothing but that absence keeps it disabled. Each
    parked item therefore carries a marker (``.agflow-disabled``), and every doctor run checks the two ways the
    arrangement fails: a marked item sitting in a scanned folder again, and a parking area inside a folder
    Antigravity scans.
    """
    issues = []
    for item in inv.items:
        if "leaked_marker" not in item.meta:
            continue
        cli = {"skill": "skills", "agent": "agents", "rule": "rules"}.get(item.kind.value, item.kind.value)
        issues.append(_warn("disabled.marker_leak",
                            f"{item.kind.value} '{item.name}' was disabled by agflow but is back in "
                            f"{item.path.parent}, a location Antigravity scans, so Antigravity loads it: it is active "
                            "despite being disabled. If you moved it back on purpose run "
                            f"`agflow {cli} enable {item.name}` (removes the marker); otherwise move it out again",
                            item.path))
    for state_dir in inv.state_dirs:
        parking_root = state_dir / "disabled"
        for lay in inv.layouts:
            for scanned in (lay.skills_dir, lay.agents_dir, lay.rules_dir, lay.plugins_dir, *lay.workflows_dirs):
                if scanned is not None and _under(parking_root, scanned):
                    issues.append(_warn("disabled.in_scan_scope",
                                        f"the parking area for disabled items ({parking_root}) lies inside {scanned}, "
                                        "which Antigravity scans, so \"disabled\" skills, agents and rules there are "
                                        "still loaded. Move agflow's state directory out of that folder",
                                        parking_root))
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
    from agflow.graph.schema import ID_RE, GraphError, load_graph  # local import: graph depends on validate

    try:
        graph = load_graph(item.path)
    except GraphError as exc:
        return [_err("graph.invalid", str(exc), item.path)]
    issues = []
    for name in graph.inputs:
        # F24b: allowed today, an error from 0.5.0 (decision D1; moved from 0.4.0 so that nothing that loads breaks)
        if not ID_RE.match(name):
            issues.append(_warn("graph.input.name",
                                f"input '{name}' of graph '{graph.id}' does not match {ID_RE.pattern} (like node ids: "
                                "lowercase, starting with a letter). It works today, but agflow 0.5.0 will reject it: "
                                "rename it and every reference to it", item.path))
        elif "-" in name:  # N4: a valid name that templates cannot write with a dot
            issues.append(_info("graph.input.hyphen",
                                f"input '{name}' of graph '{graph.id}' contains a hyphen: a template must read it as "
                                f"inputs['{name}'] ({{{{ inputs.{name} }}}} subtracts); with an underscore both "
                                "forms work",
                                item.path))
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
