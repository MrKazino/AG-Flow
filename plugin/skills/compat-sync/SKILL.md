---
name: compat-sync
description: Check that AG-Flow still works with the installed Google Antigravity, explain any drift, and update AG-Flow safely when asked. Use after the user updates Antigravity, when hooks, skills, the plugin or the MCP server stop behaving, when asked "is AG-Flow up to date" or "does it work with my Antigravity version", or when agflow doctor reports a compatibility finding.
---

# Compatibility and updates

AG-Flow depends on interfaces Google owns (hook events and decisions, the plugin manifest, MCP config, sidecars). Google changes them between releases, so AG-Flow checks them instead of assuming. **You only read and report. Changes happen when the user says yes.**

## Is it compatible? (read-only)
1. Run `agflow sync check` and read the whole output.
2. Say what it found, in these terms:
   - **ok** - the vocabulary AG-Flow was verified against is intact. Nothing to do.
   - **drift** - Antigravity changed in a way AG-Flow was not verified against, but nothing AG-Flow depends on is gone. Offer step 3.
   - **breaking** - something AG-Flow depends on is gone from Antigravity's documentation. Say which component is affected (the output names it). Offer step 4; until then that component may misbehave.
   - **unknown** - Antigravity or its documentation was not found. Say so; it is not a failure.
3. For **drift**, with the user's yes: run `agflow doctor` first. If it reports no AG-Flow errors, run `agflow sync apply`, which runs doctor and the live hook probe again and then records this Antigravity as verified. It writes nothing else.
4. For **breaking**, or when the user asks about updates: run `agflow update check`, then `agflow update plan`. Show the plan, including anything it blocks and why.

## Updating AG-Flow
- Only on the user's explicit yes, run `agflow update apply`. It downloads the release from GitHub, checks its checksums, tries the new code on a scratch copy, backs up, installs, refreshes the plugin keeping the user's edited files, verifies, and restores the backup itself if anything fails. Tell the user to restart Antigravity afterwards.
- If an update was interrupted, `agflow update status` says so; `agflow update rollback` restores the last backup (ask first: edits made since are lost).
- Never run `agy update` or change Antigravity yourself. Updating Antigravity is the user's decision.
- Never edit files under `~/.gemini` by hand to "fix" a compatibility finding; use the commands above, which back up and can be undone (`agflow fix --undo`).

## Report
Give the user: the status word, the Antigravity version found and the one AG-Flow was verified against, each finding in one line with the component it affects, and the single next step you recommend. Quote command output; do not paraphrase numbers.
