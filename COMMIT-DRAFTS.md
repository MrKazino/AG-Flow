# Commit drafts for the uncommitted changes

None of the work below has been committed or pushed; it is all in the working tree.
Each section is one logical change: a suggested commit message (no co-author trailer) and the files it touches.
`FIXLOG.md` and `CHANGELOG.md` hold one hunk per finding, so use `git add -p FIXLOG.md CHANGELOG.md` to split them.
Several findings touch the same file (for example `runner/backends.py`, `validate.py`, `cli.py`, `hooks.py`,
`app.js`): commit them in the order of this file and split the shared files with `git add -p`.
`PLAN.md` §12 also gets deviation notes as they happen. Delete this file before you commit; it is not part of the repo.

The 18 commits already on the branch `ag-flow-super-intelligence-si5028` (local only, never pushed) were rewritten so that
their messages carry no co-author line; their files, authors and dates are unchanged.

---

## F18 — bound the MCP server's workspace cache

Files: `src/agflow/mcp_server.py`, `tests/test_mcp_server.py`, `CHANGELOG.md` (F18 hunk), `FIXLOG.md` (F18 hunk)

```
fix(mcp): bound the run-to-workspace cache of the long-lived MCP server (F18)

Every graph_start and graph_resume added an entry to a module-level dict that
was never pruned, in a server Antigravity keeps open for as long as it runs.

The cache is now a lock-protected least-recently-used map capped at the same
500 entries as the on-disk run index. It stays in memory so resolution still
works if the disk index cannot be written, and a run that falls out of it is
still found through the index that RunStore.create writes.
```

---

## F19 — hand the CLI's plan to `fixes.apply()` instead of rebuilding it

Files: `src/agflow/fixes.py`, `src/agflow/cli.py`, `tests/test_fixes.py`, `CHANGELOG.md` (F19 hunk), `FIXLOG.md` (F19 hunk)

```
perf(fixes): stop apply() rediscovering the setup when the caller already has the plan (F19)

fixes.apply() always called build_plan() - a complete discovery plus validation
of every scope - and then ran a second discovery to re-check the result. The
CLI had built and shown that very plan moments earlier, so `agflow fix --apply`
scanned three times.

apply() takes an optional plan; the CLI passes the one it just built, leaving
only the re-check after the changes, which looks at the new state and cannot be
skipped. Without a plan a fresh one is built, which is what the dashboard needs
because its preview and its click can be far apart.
```

---

## F13 — an unanswered installer question is not consent

Files: `scripts/easy_install.py`, `easy-installer.bat`, `tests/test_easy_install.py`, `CHANGELOG.md` (F13 hunk, under "Changed"), `FIXLOG.md` (F13 hunk)

```
fix(installer): stop treating "no terminal" as "yes" in confirm() and the sh bootstrap (F13)

When stdin was not a terminal, confirm() returned True, so a script, pipe or CI
job that forgot --yes still stopped agflow's processes and installed the
plugin without anyone having approved it. The POSIX half of easy-installer.bat
did the same before installing uv.

Without --yes and without a terminal both now stop with a clear "re-run with
--yes" message; with a terminal they ask as before. CI already passes --yes.
cmd.exe cannot distinguish an empty answer from a missing console, so the batch
half's single uv prompt is unchanged (documented in the changelog).
```

---

## F12 — stop only agflow's own processes, and only the ones that were listed

Files: `scripts/easy_install.py`, `tests/test_easy_install.py`, `FIXLOG.md` (F12 hunk). (No CHANGELOG entry: installer-internal.)

```
fix(installer): match processes by executable path and re-verify identity before stopping them (F12)

On Windows the installer stops agflow's running processes so uv can replace
files. Its WMI query also matched any process whose command line merely
contained the tool-environment path, so an editor or terminal with that folder
open was stopped too, and PIDs were killed without checking that they still
belonged to the processes that had been listed (Windows reuses PIDs).

The query now matches the executable path only (inside the tool environment, or
the launcher) and records each process's creation time. Immediately before
taskkill the PIDs are re-queried and only processes with the same executable and
start time are stopped; a reused PID is reported and left alone.
```

---

## F20 — latest-wins guard for dashboard navigation and graph validation

Files: `src/agflow/dashboard/static/seq.js` (new), `src/agflow/dashboard/static/app.js`, `src/agflow/dashboard/static/index.html`, `tests/js/seq.test.mjs` (new), `tests/test_dashboard_js.py` (new), `CHANGELOG.md` (F20 hunk), `FIXLOG.md` (F20 hunk)

```
fix(dashboard): drop responses that arrive after a newer navigation or edit (F20)

route() and the graph editor's debounced validate() applied whatever response
arrived last. Opening a slow page and clicking another before it answered let
the late response overwrite the page the user had moved to, with no console
error; a slow validation of older text could likewise replace the result for
newer text.

A tiny dependency-free helper (static/seq.js) hands out tokens and says whether
one is still current. route() takes a token, aborts the previous page's
in-flight reads (GET only; a user-triggered POST is never tied to the page) and
discards stale results; a superseded run page no longer opens a live stream;
validate() has its own guard. Node unit tests cover the helper (skipped when
node is absent) and static checks cover the wiring.
```

---

## F32 — one versioned table for Antigravity's names (with a user override)

Files: `src/agflow/platform_vocab.py` (new), `src/agflow/data/platform_vocab.json` (new), `src/agflow/validate.py`, `src/agflow/runner/backends.py`, `src/agflow/hooks.py`, `src/agflow/fixes.py`, `src/agflow/cli.py`, `tests/test_platform_vocab.py` (new), `tests/test_sdk_backend.py`, `README.md`, `CHANGELOG.md` (F32 hunk), `FIXLOG.md` (F32 hunk)

```
refactor(vocab): centralise Antigravity's names in one versioned, overridable table (F32)

Tool names, the SDK tool mapping, model tiers, execution policies, rule
triggers, hook events, hook-payload keys and the workflow retirement date were
constants spread over validate.py, backends.py, hooks.py and fixes.py. A rename
on Google's side meant a silent mis-mapping and a new agflow release.

They now live in src/agflow/data/platform_vocab.json (with a vocab_version) and
every module derives from it through platform_vocab. A user can extend or
correct the table without a release via ~/.gemini/config/agflow/vocab.json (or a
workspace's .agents/agflow/vocab.json); an invalid override is ignored as a
whole and reported by doctor. Unknown tool names are reported as "unknown to
this agflow version (vocab vX)", the headless runner logs a warning instead of
silently dropping them, every model tier reads AGFLOW_MODEL_<TIER>, and the
workflow retirement date comes from the table. A golden test pins the built-in
table to the exact constants 0.2.0 shipped.
```

---

## F31 — the hook guard can no longer fail silently

Files: `src/agflow/hooks.py`, `src/agflow/hook_health.py` (new), `src/agflow/platform_vocab.py`, `src/agflow/data/platform_vocab.json`, `src/agflow/validate.py`, `src/agflow/dashboard/app.py`, `src/agflow/dashboard/static/app.js`, `README.md`, `tests/test_hooks_contract.py` (new), `tests/fixtures/hook_payloads/` (new, 9 samples + README), `tests/test_dashboard_js.py` (the overview-banner test), `CHANGELOG.md` (F31 hunk), `FIXLOG.md` (F31 hunk)

```
fix(hooks): understand Antigravity's documented payload and alarm when the guard goes blind (F31)

The pre-tool-use guard read tool name and arguments from key names agflow had
guessed. Antigravity documents a different layout (camelCase, the call under
toolCall, the workspace in workspacePaths), so for a documented payload
carrying `rm -rf /` the guard saw no command and allowed it. Any other
unreadable payload took the same silent path: "never break the agent" had been
implemented as "say nothing".

The accessors now read the documented layout and still accept the older flat
ones. A payload the hooks cannot understand (unknown layout, a renamed
CommandLine/path argument, invalid JSON, an empty payload, a crashing handler)
still returns {} so the agent is never blocked, but it now leaves evidence:
a hook-debug.jsonl line, an append-only hook-health.jsonl record (key names and
exception types only, never values; rotated at 256 KB), and a guard_unavailable
ledger event in each active run. The dashboard overview shows a red banner and
doctor reports hooks.guard_blind, naming the way to fix it without a release
(hook_payload_keys in vocab.json). AGFLOW_GUARD=off raises no alarm.

Contract tests label the provenance of every payload sample (vendor-doc or
synthetic); no payload captured from a live Antigravity is included yet, and
the fixtures README says how to add one.
```

---

## F22 — read the agent's result block with a JSON parser

Files: `src/agflow/runner/backends.py`, `tests/test_runner.py`, `CHANGELOG.md` (F22 hunk), `FIXLOG.md` (F22 hunk)

```
fix(runner): read the agent's result block with a JSON parser, not a pattern (F22)

The headless runner located the final result with a regex that ended the JSON
at the first `}` followed by a fence. A valid result whose string value
contained "} ```" was therefore reported as "no valid result JSON block", and
so was a result written as a top-level array; the node failed although the
agent had answered correctly.

The block is now read with json.JSONDecoder.raw_decode, which is string-aware
by construction, and the scan resumes after each value so a fence quoted
inside a string is never taken for a second block. The last block that
validates wins (for an array, its last valid element). Unchanged: the contract
still requires a fenced block, an invalid later block falls back to an earlier
valid one, and the failure message shows the reply's tail.
```

---

## F23 — a card without a timeout no longer crashes the SDK runner

Files: `src/agflow/runner/backends.py` (the `timeout_seconds` hunk), `src/agflow/graph/schema.py`, `tests/test_sdk_backend.py` (the `stub_agent` fixture and the F23 tests), `CHANGELOG.md` (F23 hunk), `FIXLOG.md` (F23 hunk)

```
fix(runner): give a card with no usable timeout the default and name the node on a timeout (F23)

SdkBackend.run computed `card.get("timeout_min", 30) * 60`, so a hand-built
card with `timeout_min: None` raised an unexplained TypeError before the agent
started, and the timeout message ("timed out after None min") did not say
which node it was.

timeout_seconds(card) returns the default for a missing, None, non-numeric,
non-finite or bool value and clamps the rest to the graph schema's range. The
default and the range are now constants in graph/schema.py used by both the
schema fields and the runner. The timeout result reads
"node '<id>' timed out after N min".

The tests add a stand-in for google.antigravity.Agent (no model call) that the
SDK backend tests of F21 reuse.
```

---

## F24a — repeated dependencies and inputs named like dict methods

Files: `src/agflow/graph/schema.py` (the repeated-dependency hunk; the F23 constants hunk belongs to F23), `src/agflow/graph/templating.py`, `plugin/skills/graph-design/references/schema.md`, `tests/test_engine.py`, `CHANGELOG.md` (two F24a hunks: Fixed and Changed), `FIXLOG.md` (F24 hunk), `PLAN.md` (§9 row, D-11)

```
fix(graph): resolve `inputs.items` to the input and reject a repeated dependency (F24a)

Jinja prefers a Python attribute to a mapping key, so an input named like a dict
method (items, keys, values, get, copy, update, pop) rendered as
"<built-in method items of dict object>" and made `when: inputs.items == ...`
always false. The sandboxed environment now resolves `a.b` on a mapping to the
entry `b` when there is one and otherwise as before, so `inputs.items()` still
iterates when no input is called `items`. Dunder access is still refused by the
sandbox (tests pin it).

`depends_on: [a, a]` was accepted silently; Graph validation now rejects it and
names the node and the repeated id. No bundled graph repeats a dependency.

Restricting the names of inputs (F24b) is a separate decision and is not
changed here.
```

---

## F25 — remove the dashboard's HTML sink

Files: `src/agflow/dashboard/static/app.js` (the `html:` branch hunk; other hunks belong to F20/F31), `src/agflow/dashboard/static/graph.js`, `tests/test_dashboard_js.py` (the F25 tests at the end), `CHANGELOG.md` (F25 hunk), `FIXLOG.md` (F25 hunk)

```
fix(dashboard): remove the innerHTML sinks and fail the build if one comes back (F25)

The element helper h() had an unused `html:` option that assigned innerHTML,
and graph.js used innerHTML to clear its container and to print "No nodes".
All were fed static strings, but a single future call with a graph title or a
node summary would have been stored XSS in a page that holds the dashboard
token.

h() no longer has the option and graph.js uses replaceChildren() and
createElement/textContent. A static test now fails when innerHTML, outerHTML,
insertAdjacentHTML, document.write, eval(, new Function or a string-argument
setTimeout/setInterval appears in any static/*.js file (comments stripped),
and a second test checks that the scan itself still sees real code.
```

---

## F26 — lock the guard's precedence over `allow_all()` against the real SDK

Files: `src/agflow/runner/backends.py` (the comment at `allow_all()`), `tests/test_sdk_contract.py` (new), `FIXLOG.md` (F26 hunk; there is no CHANGELOG entry — nothing a user sees changes)

```
test(sdk): lock the guard's precedence over allow_all() against the installed SDK (F26)

The headless runner's run_command guard is correct only because the SDK ranks
a specific deny above a wildcard allow whatever the list order. That was an
undocumented property of a third-party package.

Add a comment quoting the SDK's documented rule at the allow_all() site and
tests that run the SDK's own enforcement hook with the policy list build_config
really produces, as built and reversed: risky and destructive commands are
denied exactly as agflow's guard says, ordinary commands and non-command tools
pass, an exec-off agent can run nothing, and the SDK docstring that states the
rule is watched. The tests skip without the SDK; AGFLOW_REQUIRE_SDK=1 makes its
absence a failure (used by the nightly job of F33).
```

---

## F27 — doctor and the graph engine stop doing quadratic work

Files: `src/agflow/validate.py` (the reference-check and always-on hunks; the F9/F31/F32 hunks are other commits), `src/agflow/inventory.py`, `src/agflow/graph/engine.py`, `scripts/bench.py` (the `--only engine` hunk), `tests/test_validate.py`, `tests/test_engine.py` (the F27 tests), `CHANGELOG.md` (F27 hunk), `FIXLOG.md` (F27 hunk)

```
perf(validate): make the reference check and the engine's `when` evaluation linear (F27)

`agflow doctor` took 65 s on a 350-item plugin. A profile put most of it in the
skill reference check, which listed and is_dir()-checked every other skill
directory before testing its first candidate, for every reference. The graph
engine rebuilt and re-rendered every node's `outputs` templates for each `when`
it evaluated.

validate: one _FsCache per pass lists the skills root once, remembers existence
answers, tries the skill, its parent and grandparent before any sibling and
skips the candidates of a base directory that lacks the reference's first path
component. The answers are unchanged (a differential test against the old
implementation covers hits, misses, extensionless names, `..` and case).
Discovery measures a rule file's size, so the always-on budget no longer reads
it a third time.

engine: advance() uses a pass-local context. Outputs are rendered once per
pass; the node view is rebuilt per evaluation because earlier decisions in the
same pass change it. engine.context() is unchanged; 60 random graphs give the
same decisions as the old per-`when` context.

Measured: doctor 65,364 -> 1,251 ms; 50 `when`s on a 101-node graph 8,110 ->
103 ms. scripts/bench.py gains --only engine.
```

---

## F29 — one palette, one table builder, one undo, public install helpers

Files: `src/agflow/install.py`, `src/agflow/fixes.py` (the renamed calls), `src/agflow/dashboard/static/app.js` (the table/undo hunks), `src/agflow/dashboard/static/app.css`, `tests/test_dashboard.py`, `tests/test_fixes.py`, `tests/test_install_atomic.py` (renamed calls only), `CHANGELOG.md` (F29 hunk), `FIXLOG.md` (F29 hunk), `PLAN.md` (§9 rows, D-12)

```
refactor(dashboard,fixes): define the palette, the component table and undo once; make install's helpers public (F29)

fixes.py called install._manifest_path, _plugin_target, _BLOCK_RE and
_master_rule; install now publishes manifest_path, plugin_target, master_rule
and BLOCK_RE and fixes.py uses only those (a test fails on any underscore name).

app.js: componentTable()/componentPage() are the one table builder (the rules
page used to carry its own copy) and undoFix() is the one undo path for the
doctor banner and the result drawer.

app.css: every theme colour is a single light-dark() token under
`color-scheme: light dark`; the manual theme switch only forces color-scheme, so
the 17 dark colours are no longer written twice. This needs a 2024+ browser.
Checked in both themes in a real browser.
```

---

## F30 — specify the MCP tools once and test every other document against it

Files: `plugin/skills/graph-run/SKILL.md`, `plugin/GEMINI.md`, `plugin/agents/orchestrator.md`, `prompts/orchestrate.md`, `README.md` (the loop hunk), `tests/test_docs_sync.py` (new), `CHANGELOG.md` (F30 hunk), `FIXLOG.md` (F30 hunk)

```
docs(mcp): keep the tool signatures in one table and test every other mention against it (F30)

GEMINI.md, the orchestrator agent, the orchestrate prompt, the README and the
graph-run skill each restated the MCP tools' arguments, differently from each
other and from the server (graph_report with or without followups, graph_runs
with or without workspace, graph_start with positional or named arguments).

The graph-run skill now holds a "Tool reference" table with every tool's exact
signature (name(required, optional?)); the other documents name tools bare and
point there. tests/test_docs_sync.py fails when the table differs from the tools
the server registers (read through an MCP client), when a document mentions an
unknown graph_* name, or when it restates an argument list that is not the
canonical one; the checker itself is tested on samples. The committed documents
contained 20 mentions it rejects.
```

---

## F28 — coverage gate, Windows/macOS CI jobs, no sleeping tests

Files: `pyproject.toml`, `uv.lock`, `.github/workflows/ci.yml`, `README.md` (the `--cov` line), `tests/test_easy_install.py`, `tests/test_runner.py` (the interrupt test), `tests/test_ci_config.py` (new), `CHANGELOG.md` (F28 hunk), `FIXLOG.md` (F28 hunk)

```
ci(test): measure coverage, run the suite on Windows and macOS, stop tests waiting on the clock (F28)

Nothing measured how much of agflow the tests exercise, CI ran on Ubuntu only
although the product targets Windows first, and two tests slept to get a
result they should have waited for.

pytest-cov joins the dev group. The test job now runs on ubuntu-latest (3.10,
3.12, 3.13, 3.14), windows-latest and macos-latest (3.12) with bash as the step
shell; the Ubuntu 3.12 job fails below 84 % coverage (measured 89 % on Windows
and Linux). The floor is in CI, not in addopts, so a single-file run does not
fail on it. test_find_wheel_and_requirement sets the two file times with
os.utime; test_interrupt_releases_running_nodes waits for an asyncio.Event that
FakeBackend.on_run sets and cancels at exactly that point; both assert what they
asserted before. tests/test_ci_config.py keeps the matrix, the floor and the
sleep-free suite from regressing. The workflow is not run on real runners here.
```

---

## F21 — test SdkBackend.run against a stand-in for the SDK

Files: `tests/test_sdk_run.py` (new; it also holds the F23 run-level and timeout-helper tests), `tests/test_sdk_backend.py` (the run-level block of F23 moved out), `FIXLOG.md` (F21 hunk and the amended F23 lines). No CHANGELOG entry — tests only.

If you commit F23 before F21, the F23 tests are in `tests/test_sdk_backend.py` at that point; this file move is the only difference. If you prefer one commit for both, use the F23 message and mention F21.

```
test(sdk): exercise SdkBackend.run without the SDK or a model (F21)

SdkBackend.run, the code that decides whether a node succeeded, was never
executed by a test: the SDK tests needed the real package and stopped after
building a config.

tests/test_sdk_run.py replaces google.antigravity with a small stand-in (Agent
as an async context manager whose chat() the test controls) and build_config
with a sentinel, so run is tested on its own and with no SDK installed. It
covers a structured result, a structured dict, five kinds of invalid structured
output falling back to the text block, no structured output, a reply with no
result at all, the prompt and config reaching the agent, an exception closing
the agent, a config that cannot be built, a real timeout cancelling the agent's
work and naming the node, a missing SDK and an unknown agent. backends.py goes
from 79 % to 99 % in the full suite. The timeout tests of F23 live here too.
```

---

## F35 — probe Gemini CLI's files before `sync` writes anything

Files: `src/agflow/sync.py`, `src/agflow/cli.py` (the `sync gemini-cli` hunk), `README.md` (the command's line), `tests/test_sync.py` (new), `CHANGELOG.md` (F35 hunk), `FIXLOG.md` (F35 hunk)

```
fix(sync): check Gemini CLI's files before writing and refuse with a clear message (F35)

With a list or null where Gemini CLI's settings.json expects `mcpServers`, the
sync crashed with a TypeError after copying every skill and before writing its
record, so the next run treated all of them as foreign. Invalid JSON and a
non-object file failed the same way, as raw tracebacks.

probe_gemini_cli runs before the first write, in a dry run too. It refuses with
SyncError (which file, what was expected, "Nothing was written.") when the
settings are unreadable, not JSON, not an object, when `mcpServers` is not an
object of objects, when the skills destination is not a directory, or when
agflow's own record is damaged. If `gemini` is on PATH its version is shown
(3 s timeout, no stdin); failing to run it is only a note. The command reports a
SyncError as an error with exit code 1. A record without a "skills" or "mcp" key
is read as empty. The sync had no tests: tests/test_sync.py adds 23 cases,
including a byte-for-byte check that a refused sync leaves nothing behind.
```

---

## F34 — markers make "disabled" survive things that move items around

Files: `src/agflow/parking.py` (new), `src/agflow/toggles.py`, `src/agflow/inventory.py`, `src/agflow/validate.py` (the `_parking_issues` hunk and its call), `README.md` (the disabling paragraph), `tests/test_parking.py` (new), `CHANGELOG.md` (F34 hunk), `FIXLOG.md` (F34 hunk)

```
fix(toggles): mark parked items so a disabled item that comes back is reported and a lost index entry is recoverable (F34)

"Disabled" meant only "moved where Antigravity does not look", with nothing on
the item. A disabled item put back by hand was an enabled item agflow still
listed as parked; an index entry that was lost left a parked item nothing
listed and enable could not find; nothing checked that the parking area was
not inside a scanned folder.

disable() now writes a .agflow-disabled marker (JSON: kind, name, scope,
original path, time) inside a parked folder or beside a parked file, after the
move; enable() removes it before moving back. The inventory treats an item
that carries a marker in a scanned location as disabled and lists a marked item
in the parking area that has no index entry (also with a corrupt index);
enable() falls back to the marker without overwriting anything. Doctor warns on
every run: disabled.marker_leak (the item is active despite being disabled) and
disabled.in_scan_scope (the parking area lies inside a scanned folder).
Items parked by older versions have no marker and work as before.
```

---

## F33 — contract tests against the real SDK, an SDK surface snapshot and a nightly job

Files: `tests/test_sdk_contract.py` (the F33 part; the F26 part is already in the F26 commit if you committed in order — otherwise one commit), `tests/snapshots/google_antigravity_0.1.20.json` (new), `tests/snapshots/run_report_keys.json` (new), `tests/fixtures/sdk_smoke_graph.yaml` (new), `tests/conftest.py` (the option), `tests/test_report_schema.py` (new), `tests/test_ci_config.py` (the nightly tests), `scripts/report_schema.py` (new), `.github/workflows/sdk-contract.yml` (new), `README.md` (the `--update-sdk-snapshot` line), `FIXLOG.md` (F33 hunk). No CHANGELOG entry — tests and CI only.

```
test(sdk): pin the SDK surface agflow relies on and check the newest release every night (F33)

The Antigravity SDK is an optional extra whose API belongs to Google, and
nothing but a failing production run would have shown a release that renamed
something agflow uses.

tests/test_sdk_contract.py now checks the parts agflow calls (the tool names it
maps, the config fields it sends, the async context manager and the awaited
response methods) and compares the SDK's whole public surface with a committed
snapshot, with a readable diff and `pytest --update-sdk-snapshot` to accept a
change. Annotations are normalised so one snapshot is identical on Python 3.10
to 3.14.

.github/workflows/sdk-contract.yml runs every night and on demand against the
newest published google-antigravity (not the locked one), with AGFLOW_REQUIRE_SDK=1
so a missing SDK fails. Its live step runs a one-node smoke graph with the real
SDK and compares the report's keys with a snapshot (scripts/report_schema.py)
only when the GOOGLE_API_KEY secret is set; otherwise it warns, and fails on a
manual run. The workflow has not been run on GitHub.
```

---

## Phase 4 — sync audits (three commits, in this order)

### 4a — `fix(install): keep a Windows GEMINI.md's line endings through install and uninstall (S7)`

Files: `src/agflow/install.py` (the `_read_keeping_newlines` hunks only), `CHANGELOG.md` (the S7 hunk under Fixed)

```
fix(install): keep a GEMINI.md's own newline style through install and uninstall (S7)

Merging agflow's block into ~/.gemini/GEMINI.md read the file in text mode, so a
file with Windows (CRLF) line endings came out of `agflow install`, and out of
`agflow uninstall`, with LF: every line of a version-controlled dotfile changed.
Found by the install -> uninstall round-trip audit.

The file is now read as bytes, worked on with LF and written back with the
newline style it already had. A file that does not end with exactly one newline
still gains or loses trailing blank lines, as before; the backups under
.agflow-backups/ are kept after an uninstall, on purpose.
```

### 4b — `refactor(paths): name Antigravity's layout only in paths.py (S1, S4)`

Files: `src/agflow/paths.py` (`agy_cli_mcp_file`), `src/agflow/install.py` (the `plugin_layout` hunks), `src/agflow/fixes.py` (the two `plugin_layout` lines), `src/agflow/mcp_registry.py`, `scripts/bench.py` (the layout and `command_tool` hunks)

```
refactor(paths): build bundle and agy-cli paths through paths.py (S1, S4)

install.py, fixes.py, mcp_registry.py and scripts/bench.py spelled the plugin
bundle's layout (GEMINI.md, rules/, agents/, mcp_config.json, hooks.json) and
the agy-cli MCP file themselves. They now use paths.plugin_layout() and the new
paths.agy_cli_mcp_file(), and bench.py reads the command tool's name from the
platform vocabulary. No behaviour change.
```

### 4c — `test(sync): add the S1-S10 sync audits and register the ledger's events`

Files: `tests/test_sync_audits.py` (new), `src/agflow/graph/store.py` (`LEDGER_EVENTS`), `pyproject.toml` (the `sync_audit` marker), `tests/fixtures/hook_payloads/README.md` (the file table), `README.md` (the `agflow version` line), `tests/test_sdk_contract.py` and `scripts/report_schema.py` (the LF writers), `tests/snapshots/*.json` (rewritten with LF), `FIXLOG.md` (Phase 3 gate and Phase 4 sections), `PLAN.md` (§9 rows, D-8 to D-14)

```
test(sync): add the S1-S10 repository audits and register the ledger's events

Ten machine checks that the parts of agflow that describe the same thing agree
(`pytest -m sync_audit`): where Antigravity's paths are named, the run schema
and the ledger's vocabulary, the hook contract, the platform vocabulary, the
MCP tool documentation, the dashboard's calls against its routes, the
install/uninstall round trip, regenerated goldens, the README's measurable
claims and the FIXLOG's doctor-rule cells. graph.store.LEDGER_EVENTS lists the
twenty event names a run's ledger can contain. The audits found: CRLF snapshots
written on Windows, a missing `agflow version` README line, and (fixed in the
commits before this one) path literals and the CRLF GEMINI.md bug.
```

---

## H1 — post-audit follow-up: hooks that run, and cannot fail the agent (three commits, in this order, before Phase 5)

Found after the audit, from Antigravity's own CLI log: agflow's `hooks.json` was in a layout Antigravity ignores (only
`Stop` ran), and a stale `agflow.exe` made that hook exit 1, which Antigravity reports as a failed hook. See FIXLOG H1.
Shared files (`hooks.py`, `install.py`, `validate.py`, `fixes.py`, `cli.py`, `README.md`) also carry earlier findings:
split them with `git add -p`.

### H1a — `perf(hooks): load nothing heavy when no run is active, and keep a run nobody works on out of the way`

Files: `src/agflow/hooks.py`, `src/agflow/__main__.py`, `src/agflow/cli.py` (the `hook` command), `src/agflow/paths.py` (`RUN_INDEX_FILE`, `RUN_INDEX_VERSION`), `src/agflow/graph/store.py` (uses them), `tests/test_hook_robustness.py`, `CHANGELOG.md` (the speed and Stop hunks), `FIXLOG.md` (H1 hunk, shared with H1b/H1c)

```
perf(hooks): stop paying for urllib, traceback, YAML and the run store on every call (H1)

A hook runs on every matching tool call. python -m agflow hook imported
urllib.request (~100 ms), traceback (~45 ms) and a YAML parser, and
PostToolUse/Stop imported the whole run store with pydantic (~340 ms) before
finding that no run was active.

The imports are gone from the common path and a session with no running run
answers from one directory listing and the run index (any doubt falls back to
the full store). A call fell from about 0.3 s (PreToolUse) and 0.65 s
(PostToolUse, Stop) to about 0.2 s on Windows 11, and from 0.2 s to 0.05 s on
Linux.

The same pass makes the process contract explicit: stdin is read as UTF-8 (a
workspace folder with non-ASCII letters arrived as mojibake, so no run was
found), the answer is squeezed into the documented shapes, the exit code is
always 0, and an 8 s watchdog answers {} before Antigravity's timeout would
turn a stuck lock into an error. Stop no longer asks the agent to continue after
an error, a limit, a timeout, a cancellation or while it waits for the user, and
ignores a run untouched for 24 h (which also stops its ledger collecting
unrelated tool calls). A broken guard.yaml leaves the built-in rules on.
```

### H1b — `fix(hooks): write hooks.json in the layout Antigravity documents and make every hook command fail open`

Files: `plugin/hooks.json`, `src/agflow/hookfile.py` (new), `src/agflow/install.py`, `src/agflow/inventory.py`, `src/agflow/validate.py` (`validate_hooks_file`, `_unguarded_hook_issues`), `src/agflow/platform_vocab.py`, `src/agflow/data/platform_vocab.json`, `tests/test_hookfile.py` (new), `tests/test_install.py`, `tests/test_hooks_doctor.py` (validator and install cases), `CHANGELOG.md`, `README.md`

```
fix(hooks): write the layout Antigravity documents, and never fail the agent (H1)

Antigravity reads hooks.json as {hook name: {event: ...}} with
{matcher, hooks: [...]} groups for PreToolUse/PostToolUse. agflow wrote
{"hooks": {Event: [{matcher, command}]}}: Antigravity loaded a hook named
"hooks", ignored both tool-event entries (the guard and the run ledger never
ran) and ran only the flat Stop entry. On a machine whose agflow executable had
lost its tool environment that entry exited 1 ("failed to call custom stop
hook ... ModuleNotFoundError: No module named 'agflow'"), which ends the
agent's turn.

The installer now writes the documented layout (10 s timeout, a PostToolUse
matcher without reads) and wraps every command so that a failure of any kind
prints a note to stderr and answers {} with exit 0, in cmd and sh syntax. On
Windows a path with spaces becomes its 8.3 short name instead of quotes, which
cmd /c does not unquote the way a person typing it would. doctor reads both
layouts and reports hooks.inert (the old layout) and hooks.unguarded (commands
without the wrapper). The wrapper is executed through the OS shell by the tests.
```

### H1c — `feat(doctor): run the installed hooks to prove they start (hooks.broken)`

Files: `src/agflow/hook_probe.py` (new), `src/agflow/validate.py` (`probe_hooks`), `src/agflow/fixes.py` (`probed`, `HOOK_CODES_FIXED_BY_REINSTALL`), `src/agflow/cli.py` (`doctor`, `fix`), `tests/test_hooks_doctor.py` (probe cases), `plugin/skills/graph-run/SKILL.md`, `plugin/agents/orchestrator.md`, `README.md` (*When the agent reports an error*), `CHANGELOG.md`, `FIXLOG.md`

```
feat(doctor): run agflow's installed hook commands and report the ones that cannot start (H1)

Fail-open hides a broken install from the agent, so it must not hide it from the
person. doctor and fix now run the hook commands of the global agflow plugin
the way Antigravity does (cmd /c or sh -c, with AGFLOW_HOOK_PROBE=1 so no run is
touched) and report hooks.broken with the last line of the error. fix reinstalls
that copy and points the hooks at the agflow that is running.

Running what a file names is only safe when the file cannot be someone else's:
only the user's own global copy is probed (never a workspace file, which may
come from a cloned repository), only a line in exactly the form the installer
writes is run (one agflow call and the fixed fail-open tail, no room for a
second command or a variable), and the working directory is an empty scratch
folder. The dashboard does not probe.

The orchestrator is told to graph_resume after an infrastructure error (a
network issue, "Agent execution terminated due to error") instead of reporting
the node failed, which would spend one of its attempts on an outage.
```

---

## H2 — leftovers closed for the 0.3.0 release (four commits, in this order, after H1)

Shared files (`validate.py`, `README.md`, `CHANGELOG.md`, `FIXLOG.md`, `store.py`, `fixes.py`, `cli.py`) carry other
changes too: split them with `git add -p`.

### H2a — `feat(doctor): warn about graph input names that do not follow the node-id style (F24b, N4)`

Files: `src/agflow/validate.py` (`_validate_graph_item`), `plugin/skills/graph-design/references/schema.md`, `README.md` (*Writing your own graphs*), `tests/test_validate.py`, `CHANGELOG.md` (Added), `FIXLOG.md` (F24 hunk)

```
feat(doctor): warn about graph input names that do not follow the node-id style (F24b, N4)

The held decision D1 is taken at the default the plan stated for "no answer":
nothing that loads today stops loading. An input called Feature, bugID or 1st
now gets the graph.input.name warning, which says that 0.4.0 will reject it;
a valid name with a hyphen gets an INFO hint that a template must read it as
inputs['bug-id'], because {{ inputs.bug-id }} subtracts.

No bundled graph is flagged and a graph with such a name still loads and runs
(both pinned by tests).
```

### H2b — `fix(runner): give a node back when its result cannot be recorded (N9)`

Files: `src/agflow/runner/runner.py`, `tests/test_runner.py`, `CHANGELOG.md` (Fixed), `FIXLOG.md` (H2)

```
fix(runner): give a node back when its result cannot be recorded (N9)

A finished task was dropped from the in-flight set before its result was
written. If the write failed (a full disk, a lock that timed out) the interrupt
path never saw that node, so it stayed running until its 30-minute lease ran
out and the run was stuck behind it.

The task now stays in flight until _report has returned, so the existing
interrupt path releases it without spending an attempt. The error still stops
the runner.
```

### H2c — `fix(store): record a run whose global index entry could not be written (N1)`

Files: `src/agflow/graph/store.py` (`LEDGER_EVENTS`, `register_run`, `RunStore.create`), `tests/test_store_index.py`, `CHANGELOG.md` (Added), `FIXLOG.md` (H3)

```
fix(store): say so when the global run index cannot be written (N1)

register_run swallowed OSError. The index only lets another workspace's MCP
server find a run by id, so a failure must not fail the run, but nothing showed
that the shortcut was missing. It now returns the reason and create() writes a
run_index_unwritable ledger event.
```

### H2d — `fix(migrate): make agflow migrate and the dashboard button undoable (N3)`

Files: `src/agflow/migrate.py`, `src/agflow/fixes.py` (`migrate_undoable`), `src/agflow/cli.py` (`migrate`), `src/agflow/dashboard/app.py` (`/api/migrate`), `README.md` (the `migrate` line), `tests/test_fixes.py`, `tests/test_dashboard.py`, `CHANGELOG.md` (Added), `FIXLOG.md` (H4)

```
fix(migrate): record agflow migrate as an undoable batch (N3)

agflow fix ran the same migrations inside its backup transaction, but the CLI
command and the dashboard's migrate button moved .agent/ entries, renamed
workflows and merged 1.x MCP servers into the user's mcp_config.json with
nothing to restore from.

MigrationReport now lists every path a run touches, and fixes.migrate_undoable
backs them up and records one batch (also when the migration stops half-way),
so agflow fix --undo restores the tree byte for byte. A dry run and a run that
did nothing record nothing.
```

---

## Phase 5 — two last commits

### 5a — `ci(test): raise the coverage floor to the measured value minus one`

Files: `.github/workflows/ci.yml` (`--cov-fail-under=88`), `README.md` (the `--cov` line), and the two places the 84 appeared in `CHANGELOG.md` / `FIXLOG.md`

```
ci(test): raise the coverage floor from 84 % to 88 % (F28)

The suite measures 89.35 % on Linux with Python 3.12, the configuration of the
job that enforces the floor (89 % on Windows with Python 3.14). The floor is the
measurement minus one so that a real loss of coverage fails the build and
rounding does not. The README line and tests/test_sync_audits.py (S9) keep the
two numbers equal.
```

### 5b — `docs: final report, upgrade notes and the Phase 3 to 5 gates`

Files: `FINAL-REPORT.md` (new), `FIXLOG.md` (Phase 3 gate, Phase 4 and Phase 5 sections), `CHANGELOG.md` (the upgrade notes), `PLAN.md` (D-8 to D-14), `COMMIT-DRAFTS.md` is *not* committed — delete it

```
docs: final report of the audit remediation with the gates of phases 3 to 5

FINAL-REPORT.md follows the format of the brief: execution summary, phase log,
the 35 findings with status, commit, tests and doctor rule, the S1-S10 audit
results and the remaining risks. FIXLOG records the Phase 3 gate, the Phase 4
audits and the G3 final gate (what ran, and what could not be run here).
CHANGELOG gains upgrade notes for the user-visible changes; PLAN's deviations log
records D-8 to D-14.
```


---

## Release 0.3.0 — the last commit, the tag and the GitHub release (you run all of it)

No commit, staging, tag or push has been made for this release. Everything below is yours to run, under your
name (`Koosha KZ <91173654+MrKazino@users.noreply.github.com>` is already `git config user.name/user.email`).

### R1 — `chore(release): 0.3.0` (the last commit, after every draft above)

Files: `src/agflow/__init__.py`, `pyproject.toml` (version, homepage), `plugin/plugin.json` (version, homepage), `uv.lock` (the project's own version line), `README.md` (the example version, the repository URL), `CHANGELOG.md` (the `[0.3.0] — 2026-10-16` heading and the empty `[Unreleased]` above it)

```
chore(release): 0.3.0

The version moves from 0.2.0 to 0.3.0 (decision D5): the audit remediation
changes behaviour a user can see (the dashboard token header, the installer's
--yes, the documented hooks.json layout and the guard that now runs, the
rejected repeated dependency) and adds doctor rules, so it is not a patch.

The homepage in pyproject.toml and plugin.json and the README's install URL
follow the repository's current name (origin is github.com/MrKazino/AG-Flow).
```

### Before you commit
1. Delete `COMMIT-DRAFTS.md` when you are done with it, and move `RELEASE-NOTES-0.3.0.md` out of the repository folder (`mv RELEASE-NOTES-0.3.0.md ..`): both would otherwise be packed into the source distribution, which `uv build` takes from the working tree. `FINAL-REPORT.md` is yours to keep or drop.
2. The 18 earlier local commits already have their messages cleaned (no co-author line): nothing to do for them.

3. Check the tree once more:

```bash
uv lock --check
```

```bash
uv run ruff check src tests scripts
```

```bash
uv run pytest -q
```

### Tag and release
Merge the branch into `master` first if that is how you release (the tag belongs on the commit users will install).

```bash
git tag -a v0.3.0 -m "agflow 0.3.0"
```

```bash
git push origin master v0.3.0
```

The GitHub release, with the notes in `RELEASE-NOTES-0.3.0.md` (`gh` must be logged in as you) and the two files `uv build` produces:

```bash
uv build
```

```bash
gh release create v0.3.0 --title "AG-Flow 0.3.0" --notes-file ../RELEASE-NOTES-0.3.0.md dist/agflow-0.3.0-py3-none-any.whl dist/agflow-0.3.0.tar.gz
```

The repository has no release or publish workflow and nothing here publishes to PyPI; if you want that, it is a new
decision (the package name, a token and a workflow), not part of this change.
