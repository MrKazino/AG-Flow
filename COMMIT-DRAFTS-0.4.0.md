# Commit drafts for AG-Flow 0.4.0

Nothing here has been committed, staged, tagged or pushed. Every command below is yours to run, under your own name
(`git config user.name/user.email` already holds it). The messages carry no co-author line and no tool attribution.
Delete this file before you commit; it is not part of the repository.

## What this branch contains

The branch `ag-flow-super-intelligence-si8088` was created from the tip of `ag-flow-super-intelligence-si5028` with that
branch's uncommitted 0.3.0 work. So the working tree here holds **two layers of uncommitted work**:

1. the 0.3.0 audit remediation (F1–F35, H1–H4), whose commit drafts are in `COMMIT-DRAFTS.md`, and
2. the 0.4.0 work (H5–H10), drafted below.

Layer 1 is byte for byte what the `si5028` worktree holds; layer 2 is the difference between the two worktrees
(46 new and 41 changed files at the time the comparison was made, plus the documents `CHANGELOG.md`, `FIXLOG.md`,
`PLAN.md`, `README.md` and `docs/research-antigravity-0.4.0.md`).

## Order of work

**Recommended: keep the two layers apart.** The audit's history stays one commit per finding, and 0.3.0 can be tagged.

1. In the `si5028` worktree, make the 0.3.0 commits from `COMMIT-DRAFTS.md` (its section *Release 0.3.0* ends with the
   tag). Its `R1` commit is right for that tree: there, `plugin/plugin.json` still has the `homepage` it names.
2. In this worktree, move the branch to that new tip **without touching any file**:

   ```bash
   git reset --mixed ag-flow-super-intelligence-si5028
   ```

   `git status` then lists only the 0.4.0 layer. Check that with `git diff --stat`: no file of the audit work should
   appear unless it also changed in 0.4.0. (`--mixed` rewrites the index, never the working tree. If anything looks
   wrong, nothing is lost: the files are as they were.)
3. Delete the copies of the 0.3.0 drafts that sit untracked in this worktree: `COMMIT-DRAFTS.md`, `FINAL-REPORT.md`,
   `RELEASE-NOTES-0.3.0.md`.
4. Make the commits below, then the release commit, the tag and the release.

**Quick alternative: one history, no 0.3.0 release.** Skip steps 1–3, skip `R1` of the 0.3.0 drafts, and commit the whole
tree from here as the single commit under *If you prefer one commit* below (or in a few topic commits of your own
choosing). `CHANGELOG.md` still describes 0.3.0 and 0.4.0 separately; the 0.3.0 section then documents what 0.4.0 also
contains.

## The commits (0.4.0 layer, in this order)

Several files belong to more than one commit (`src/agflow/cli.py`, `mcp_server.py`, `paths.py`, `validate.py`,
`install.py`, `fixes.py`, `dashboard/app.py`, `README.md`, `CHANGELOG.md`, `FIXLOG.md`, `PLAN.md`, `plugin/profiles.yaml`,
`plugin/skills/graph-run/SKILL.md`, `tests/conftest.py` and a few test files): split them with `git add -p` using the
topic of each hunk. **Only the last commit is tested as a whole**; the commits in between may not pass the suite on
their own (for example `tests/conftest.py` counts the bundled skills). If every commit must be green, use the single
commit.

### H5 — `feat(sync): notice when Antigravity changes, with a read-only compatibility check`

Files: `src/agflow/antigravity_probe.py`, `src/agflow/compat.py`, `src/agflow/data/compat_baseline.json`,
`scripts/make_compat_baseline.py`, `plugin/skills/compat-sync/SKILL.md`, `tests/test_antigravity_probe.py`,
`tests/test_compat.py`, `tests/test_cli_sync.py`; hunks of `src/agflow/cli.py` (`sync check|apply|forget`),
`src/agflow/paths.py`, `src/agflow/validate.py` (`compat.*`)

```
feat(sync): notice when Antigravity changes, with a read-only compatibility check (H5)

Antigravity's hook, plugin, MCP and sidecar contracts changed in most 1.x
releases, and agflow found out by accident (H1). agflow sync check reads the
installed Antigravity's version and the documentation that ships with it,
extracts the names those documents define and compares them with a baseline of
what this release was verified against. It stores hashes and short
identifiers only, never the vendor's text.

The answer is ok, drift, breaking or unknown, names the affected component,
and exits 0, 0, 1 or 2. agflow sync apply accepts a newer Antigravity per
machine only when nothing breaking is found and agflow's own doctor and live
hook probe pass; agflow sync forget goes back to the shipped baseline.

The shipped baseline carries no path of the machine it was generated on.
```

### H6 — `feat(update): check for, verify and apply AG-Flow updates, with a backup and a rollback`

Files: `src/agflow/versions.py`, `src/agflow/release_feed.py`, `src/agflow/update.py`, `src/agflow/update_apply.py`,
`src/agflow/plugin_files.py`, `scripts/make_release.py`, `scripts/smoke_release.py`, `.github/workflows/release.yml`,
`tests/fake_github.py`, `tests/fake_update_env.py`, `tests/test_versions.py`, `tests/test_release_feed.py`,
`tests/test_update.py`, `tests/test_update_apply.py`, `tests/test_cli_update.py`, `tests/test_plugin_files.py`,
`tests/test_make_release.py`, `tests/test_release_config.py`; hunks of `src/agflow/cli.py` (`update ...`),
`src/agflow/fixes.py` (`restore_ops`, `Txn.rollback`, `drop_batch`), `src/agflow/install.py` (`plugin_files.record`)

```
feat(update): check for, verify and apply AG-Flow updates, with a backup and a rollback (H6)

agflow update check|plan|apply|rollback|status|config. Updates come only from
the GitHub Releases of MrKazino/AG-Flow (the name agflow on PyPI is not this
project): HTTPS only, a host allowlist, no ambient token, SHA256SUMS plus
GitHub's own digest, the wheel inspected as a zip before anything is
installed, release notes treated as untrusted text.

apply backs up the installed package and every plugin file the installer
touches, installs the wheel, refreshes each install with the new code, puts
files you edited back on top (a conflict is saved under incoming/), then runs
the new doctor and hook probe. Any failure restores the backup; rollback
restores it later, also after an interrupted update. Nothing is applied
without the user's confirmation, and Antigravity itself is never touched.

scripts/make_release.py builds and checks the wheel, sdist, SHA256SUMS and
release.json; scripts/smoke_release.py proves the path with real wheels in a
throwaway environment and a sandboxed home folder; the release workflow is a
draft that publishes a v* tag.
```

### H7 — `feat(graph): check agent reports against the workspace; add the verifier and a grounding protocol`

Files: `src/agflow/graph/verify.py`, `plugin/agents/verifier.md`, `plugin/skills/verify-claims/SKILL.md`,
`plugin/skills/deep-research/SKILL.md`, `plugin/GEMINI.md`, `plugin/rules/orchestration.md`,
`plugin/graphs/feature-delivery.yaml`, `plugin/graphs/security-sweep.yaml`, `plugin/graphs/ui-feature.yaml`,
`tests/test_evidence.py`; hunks of `src/agflow/graph/schema.py`, `graph/engine.py`, `graph/store.py`,
`src/agflow/mcp_server.py`, `src/agflow/runner/runner.py`, `runner/backends.py`, `plugin/agents/orchestrator.md`,
`plugin/profiles.yaml`, `plugin/skills/graph-run/SKILL.md`, `plugin/skills/graph-design/references/schema.md`,
`prompts/orchestrate.md`

```
feat(graph): check agent reports against the workspace; add the verifier and a grounding protocol (H7)

graph_report used to accept "succeeded" and a summary. The engine now looks at
the workspace first (it reads files and runs nothing): declared outputs and
reported artifacts must exist and not be empty, declarative checks must hold,
and the summary must say something checkable. A node's evidence mode (off,
advisory, required) decides what a failure means; a refused report goes back
with the exact missing items. The default is advisory, so existing graphs
behave as before; the bundled graphs use required where they declare outputs.

A read-only verifier agent re-checks claims other agents made (skills
verify-claims and deep-research), and the master prompt gains a grounding
protocol: a claim needs a source that was opened or a command that was run, and
reports separate verified, inferred and unknown.

This makes an unsupported claim visible and harder to pass on. It does not
measure or guarantee a hallucination rate.
```

### H8 — `feat(learn): learn per workflow and propose changes the user approves`

Files: `src/agflow/learn.py`, `src/agflow/improve.py`, `src/agflow/workflows.py`, `plugin/skills/retrospective/SKILL.md`,
`plugin/skills/workflow-manager/SKILL.md`, `tests/test_learn.py`, `tests/test_improve.py`; hunks of
`src/agflow/cli.py` (`improve ...`, `workflows`), `src/agflow/mcp_server.py` (`graph_lessons`, `graph_overview`)

```
feat(learn): learn per workflow and propose changes the user approves (H8)

When a run finishes, agflow records per workflow what went wrong (a step the
evidence check sent back, a reviewer's rejection, a timeout): redacted, bounded
and deterministic, no model involved. The next dispatch of an affected step
carries the lessons that recurred.

agflow improve turns a problem seen in at least two runs within 90 days into a
proposal (a longer limit, another retry, evidence required, an acceptance
criterion) with the evidence for each change and the exact diff. Nothing is
applied automatically: apply validates, backs up and writes a workspace copy of
the workflow (the plugin's own file is never edited); reject suppresses the
same change for 30 days; undo reverts it, as does agflow fix --undo.
```

### H9 — `feat(manager): a side-pane panel, workflows overview and a daily check inside Antigravity`

Files: `src/agflow/sidecars.py`, `src/agflow/maintenance.py`, `src/agflow/dashboard/static/manager.js`,
`plugin/sidecars/manager/sidecar.json`, `plugin/sidecars/maintenance/sidecar.json`, `plugin/plugin.json`,
`tests/test_manager.py`, `tests/test_manifest_and_health.py`; hunks of `src/agflow/dashboard/app.py`, `app.js`,
`index.html`, `app.css`, `src/agflow/install.py` (`_wire_sidecars`), `src/agflow/paths.py`, `src/agflow/validate.py`
(manifest rules, health notes), `tests/test_sync_audits.py` (S6 scans `manager.js`)

```
feat(manager): a side-pane panel, a workflows overview and a daily check inside Antigravity (H9)

Antigravity's Settings screen is not an extension point; a plugin adds UI as a
sidecar whose web panel opens in the side pane. The plugin now ships
agflow/manager (agflow dashboard --sidecar: loopback only, on the port and with
the token Antigravity provides) and agflow/maintenance (a scheduled sidecar
that runs agflow maintenance run once a day: checks only, no model, no
conversation). Both are off until the user switches them on; agflow never does
it, and agflow panel status prints the steps.

The dashboard gains a Workflows page (what each workflow needs and where it was
found, runs, lessons, proposals with approve / decline / undo) and a Sync &
updates page. plugin.json keeps only the fields Antigravity documents; doctor
reports the others and shows the cached compatibility and update notes.

The panel's handshake follows the vendor's sidecar runtime and was verified in
a browser, not inside a live Antigravity window.
```

### H10 — `perf: start faster`

Files: `src/agflow/frontmatter.py`, `tests/test_startup.py`, `tests/test_frontmatter.py`; hunks of
`src/agflow/__main__.py`, `src/agflow/cli.py` (lazy imports), `src/agflow/hook_probe.py`, `src/agflow/mcp_server.py`,
`scripts/bench.py`, `README.md` (the start-up table)

```
perf: start faster; measure it (H10)

agflow version no longer loads the CLI (560 ms to 94 ms on the development
machine), asyncio and the updater and manager modules are loaded only by the
commands that use them, the hook probe runs its three commands in parallel
(agflow doctor 3.2 s to 1.7 s, together with the other changes), and the MCP server loads the manager's modules
when their tool is called. scripts/bench.py --only startup reproduces the
table in the README; the tests pin what is imported rather than wall time.

Discovery and validation parsed the YAML front matter of every item on every
call. frontmatter.parse now keeps the parsed mapping of each distinct text,
keyed on the text itself and never on a file's time stamp, so an edited file
cannot be served stale. On a 450-item workspace the dashboard overview goes
from 871 ms to 429 ms; the first call of a process is unchanged.
```

### Docs — `docs: 0.4.0 changelog, fixlog, plan decisions and research note`

Files: `CHANGELOG.md` (the `[0.4.0]` section), `FIXLOG.md` (H5–H10, A7–A13, the mutation checks), `PLAN.md` (D-17 to D-19), `README.md`,
`docs/research-antigravity-0.4.0.md`, `RELEASE-NOTES-0.4.0.md`

```
docs: 0.4.0 changelog, fixlog, plan decisions and a research note

States what changed, the evidence behind each change and what was not
verified: no live Antigravity window, no published release yet, no measured
effect on hallucination. D-18 moves the rejection of non-conforming graph input
names (F24b) from 0.4.0 to 0.5.0; D-19 lists where the result differs from the
approved plan; the FIXLOG records the amended tests and the mutation checks of
the safety rules.
```

### If you prefer one commit

```
feat: AG-Flow 0.4.0 - Antigravity sync, safe updates, evidence checks, per-workflow learning, manager panel

Keeps agflow in step with Antigravity (agflow sync), updates it only when the
user confirms, with a backup, verification and rollback (agflow update),
checks what agents report against the workspace and adds a read-only verifier
and a grounding protocol, learns per workflow and proposes changes that the
user approves (agflow improve), and adds a side-pane manager and a daily check
as sidecars that stay off until the user switches them on. Start-up is faster.

See CHANGELOG.md and FIXLOG.md (H5-H10) for the evidence behind each change and
for what was not verified.
```

### Release — `chore(release): 0.4.0`

Files: `src/agflow/__init__.py`, `pyproject.toml`, `plugin/plugin.json` (the version line; if H9 is already in, only that line), `uv.lock` (the project's own version line)

```
chore(release): 0.4.0

The version moves from 0.3.0 to 0.4.0: new commands (sync, update, improve,
panel, workflows, maintenance), new plugin contents (two sidecars, a verifier
agent, five skills) and stricter bundled graphs make this a minor release.
```

## Before you commit

1. Delete `COMMIT-DRAFTS-0.4.0.md`. Keep `RELEASE-NOTES-0.4.0.md` where it is only if you publish through
   `.github/workflows/release.yml` (it reads that file from the repository root, and it then also lands in the source
   distribution); otherwise move it out of the folder and pass it with `--notes-file`.
2. Check the tree once more:

```bash
uv lock --check
```

```bash
uv run ruff check src tests scripts
```

```bash
uv run pytest -q
```

## Tag and release

Merge the branch into `master` first if that is how you release (the tag belongs on the commit users will install).

```bash
git tag -a v0.4.0 -m "agflow 0.4.0"
```

```bash
git push origin master v0.4.0
```

With the draft workflow committed, the tag push builds the wheel and sdist, writes `SHA256SUMS` and `release.json`, runs
the update smoke test on the built wheels, attests their provenance and creates the GitHub release with
`RELEASE-NOTES-0.4.0.md`. By hand instead (`gh` logged in as you):

```bash
uv run python scripts/make_release.py
```

```bash
gh release create v0.4.0 --title "AG-Flow 0.4.0" --notes-file RELEASE-NOTES-0.4.0.md dist/release/*
```

The release **must carry `SHA256SUMS`** (the updater refuses a wheel that is not listed there and matching) and should
carry `release.json`; both are in `dist/release/`. After it exists, `agflow update check` on any 0.4.0 install reports
"up to date"; the first real update happens when a later release is published.

## After release

* `agflow install` once on each machine, then `agflow panel status` to see the two sidecars; they stay off until you
  switch them on.
* The `agflow` launcher installed on the development machine is still the broken one described in FIXLOG H1 (its tool
  environment was deleted). `agflow fix`, or `uv tool install --force --reinstall <checkout>`, repairs it; neither was
  run.
