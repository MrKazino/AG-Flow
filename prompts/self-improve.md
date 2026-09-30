# Self-improve prompt — audit and upgrade this workspace's agent setup

Use this when the agent ignores instructions, context feels bloated, answers are inconsistent, or
you just set up a new project.

---

```text
Use the workspace-setup skill to audit and improve how Antigravity is configured for this workspace
and for me globally.

1. Run `agflow doctor`, `agflow list --all` and `agflow status`; summarise errors and warnings.
2. Fix everything that makes Antigravity silently ignore configuration (rule triggers, skill
   name/folder mismatches, invalid hooks.json or mcp_config.json).
3. Reduce always-on context: keep GEMINI.md and always_on rules short, move situational guidance to
   model_decision or glob rules with precise descriptions, remove duplication.
4. Migrate any workflows to skills (`agflow migrate --dry-run`, then apply) — workflows are retired on
   2026-11-01.
5. Review MCP servers (`agflow mcp list`): disable the ones this project doesn't need, replace literal
   secrets with ${ENV} placeholders, list missing env vars.
6. Recommend the smallest agflow profile that fits this project and apply it after I confirm.
7. If I repeat a multi-step process here, propose capturing it as an agflow graph.

Show me a before/after table (doctor error/warning counts, number of always-on characters, enabled
skills/agents/MCP servers). Disable rather than delete anything I created.
```
