---
name: workspace-setup
description: Audit and improve this workspace's Antigravity agent setup — GEMINI.md and rules (size, triggers), skills, custom agents, MCP servers, hooks, plugins, workflow-to-skill migration and profiles — using the agflow CLI, then apply safe fixes. Use when the agent keeps ignoring instructions, context feels bloated, setup is new, or the user asks to optimise, clean up or configure Antigravity/Gemini.
---

# Workspace setup audit

## Procedure
1. **Inventory & validate**
   ```bash
   agflow doctor            # errors/warnings for every component, all scopes
   agflow list --all        # what is installed where (workspace > plugins > global)
   agflow status            # install status + active runs
   ```
2. **Fix errors first** (they make Antigravity silently ignore files): missing/invalid `trigger` in rules, `globs` missing for glob rules, skill `name` ≠ folder, missing descriptions, invalid `hooks.json` event names, invalid `mcp_config.json`.
3. **Context budget.** GEMINI.md and each rule are capped at 12,000 chars; always-on context costs tokens on every turn. Move situational guidance from `always_on` to `model_decision` (with a precise description) or `glob` rules. Remove duplication between GEMINI.md, AGENTS.md and rules.
4. **Skills quality.** Descriptions must say *what* and *when* (they drive auto-triggering). Merge overlapping skills; move long reference material into `references/`; put deterministic steps into `scripts/`.
5. **Workflows → skills.** Workflows are retired on 2026-11-01: `agflow migrate --dry-run`, review, then `agflow migrate`.
6. **MCP hygiene.** `agflow mcp list` → disable servers the project doesn't use (each adds tools to the context); replace literal secrets with `${ENV}` placeholders; check missing env vars.
7. **Profiles.** Pick the smallest profile that fits the project (`agflow profile list` → `agflow profile apply <name>`), e.g. `frontend`, `secure`, `devops`, or `core`.
8. **Graphs.** If the team repeats a multi-step process, capture it as a graph (`graph-design`).
9. **Re-run** `agflow doctor` until it reports 0 errors; summarise remaining warnings with a recommendation each.

## Rules
- Show the user a before/after summary; don't delete their custom rules/skills without asking — disable instead (`agflow skills disable <name>` is reversible).
- Back-ups: `agflow install/uninstall` and MCP edits back up files under `~/.gemini/config/.agflow-backups/`.

## Exit criteria
Doctor output before/after (error/warning counts), changes made, and recommendations not applied.
