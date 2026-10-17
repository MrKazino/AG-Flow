# Changelog

All notable changes to agflow are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project aims to follow [Semantic Versioning](https://semver.org/). Finding IDs (`F1`…`F35`) refer to the
audit registry tracked in [PLAN.md](PLAN.md) and [FIXLOG.md](FIXLOG.md); `H1` is the follow-up found after it.

## [Unreleased]

Nothing yet.

## [0.3.0] — 2026-10-16

The audit remediation (F1–F35) and the hooks follow-up (H1), with the observations N1, N3, N4 and N9 closed on top.

### Upgrade notes
- Dashboard clients must send the token in the `X-Agflow-Token` header: `?token=` is rejected on every `/api/*` route (F1).
  The live run page now exchanges the token for a one-time ticket with `POST /api/stream-ticket`.
- A script that ran `easy-installer.bat` and answered its question through a pipe must pass `--yes` now: with no terminal
  and no `--yes` the installer stops instead of agreeing for you (F13).
- A graph that lists the same dependency twice in `depends_on` no longer loads; delete the repeat (F24a).
- The dashboard needs a browser from 2024 or later (CSS `light-dark()`: Chrome/Edge 123, Firefox 120, Safari 17.5) (F29).
- agflow now also writes `.agents/agflow/runs/.index.json`, `hook-health.jsonl` and `.agflow-disabled` markers next to
  parked items; an optional `vocab.json` can extend the names agflow knows (F3, F31, F34, F32).
- **Run `agflow install` (or `agflow fix`) once after upgrading: until then your installed hooks are the old, ignored
  layout (H1).** The guard then starts to run in every session, not only when a graph run is active: it asks before a
  force-push, `git reset --hard`, `curl … | sh`, reading `.env` files and the other cases listed under *Safety hooks*,
  and denies `rm -rf /` and the like. `agflow install --no-hooks` or `AGFLOW_GUARD=off` switches that off. An
  Antigravity CLI older than 1.0.16 rejects the empty answer a hook gives when it has no opinion; with one, install
  with `--no-hooks`.
- Graph input names are still accepted as they are, with a doctor warning; agflow 0.4.0 will reject the ones that do not
  match the node-id style (F24b).

### Added
- **`agflow doctor` warns about graph input names that do not follow the node-id style (F24b, decision D1).**
  An input called `Feature`, `bugID` or `1st` gets `graph.input.name` (a warning): it works today, and agflow 0.4.0
  will reject it, so rename it and the `{{ inputs.… }}` references to it. Nothing that loads today stops loading in 0.3.0.
  A valid name with a hyphen (`bug-id`) gets an INFO hint, `graph.input.hyphen`: `{{ inputs.bug-id }}` subtracts, and
  only `inputs['bug-id']` reads the input (N4).
- **`agflow migrate` can be undone (N3).** It used to move, rename and merge your files — workflows into skills, `.agent/`
  into `.agents/`, 1.x MCP servers into `mcp_config.json` — with no way back. A real run is now one batch in the same
  history as `agflow fix`, with a byte-for-byte backup, and `agflow fix --undo` reverts it (also when it stopped half-way).
  The dashboard's migrate button does the same; a dry run records nothing.
- **A run whose entry in the global run index could not be written says so (N1).** The index only lets another
  workspace's MCP server find a run by its id, so a failure never failed the run — and was invisible. The ledger now
  carries a `run_index_unwritable` event with the reason.

### Security
- **The dashboard no longer contains a way to turn text into markup (F25).**
  The element helper had an unused `html:` option that assigned `innerHTML`, and the graph drawing code used
  `innerHTML` to clear its container. Both are replaced by DOM calls, and a test now fails if `innerHTML`,
  `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval` or `new Function` appears in any dashboard script.
- **The safety guard can no longer die silently when Antigravity's hook payload is not what agflow expects (F31).**
  Antigravity documents its hook payload (the tool call under `toolCall`, the workspace in `workspacePaths`), but
  agflow only looked for older, guessed key names: given a documented payload carrying `rm -rf /` the guard saw no
  command and allowed it. The hooks now understand the documented layout (and still accept the older ones). Any
  payload they cannot read — a changed layout, a renamed `CommandLine`, invalid JSON, a crashing handler — still
  returns `{}` so the agent is never broken, but is now recorded (`hook-debug.jsonl`, `hook-health.jsonl`, a
  `guard_unavailable` ledger event), shown as a red banner on the dashboard and as the `hooks.guard_blind` doctor
  warning, which names the way to fix it without a release (`hook_payload_keys` in `vocab.json`). The new contract
  tests label every payload sample's provenance; no sample recorded from a live Antigravity is included yet.
- **The dashboard token no longer travels in a URL (F1).** The live run view used to open its event stream with
  `?token=<dashboard token>` because `EventSource` cannot send headers, so the bearer token — which grants full
  dashboard control — was kept in browser history and in every proxy/server log. The page now trades the token
  (sent as a header) for a **one-time stream ticket** (`POST /api/stream-ticket`; about 30 s, single use, bound to
  one run, valid only for that run's `/stream`) and opens the stream with `?ticket=`. Reconnects ask for a new
  ticket. The `#token=` start-up flow is unchanged.

- **The uv bootstrap in `easy-installer.bat` is pinned and checksum-verified (F4).** When uv was missing, the
  installer ran `irm https://astral.sh/uv/install.ps1 | iex` (Windows) or `curl … | sh` / `wget … | sh`
  (POSIX): whatever the CDN served at that moment executed on your machine, unseen — no version, no checksum.
  It now downloads one specific uv release's installer to a temporary file, verifies its SHA-256 against
  `scripts/uv-pin.env`, and only then runs it; a mismatch aborts and runs nothing. New helper scripts:
  `scripts/install-uv.sh`, `scripts/install-uv.ps1`. For CI or a one-off newer uv, set `AGFLOW_UV_VERSION` together
  with `AGFLOW_UV_INSTALLER_SHA256`.

### Changed
- **CI measures coverage, runs on Windows and macOS too, and two timing-dependent tests no longer wait on the clock (F28).**
  `pytest-cov` is a dev dependency; the Ubuntu 3.12 job fails below 88 % coverage (the suite measures 89 %). The test job
  now also runs on `windows-latest` and `macos-latest`. A test that slept 10 ms to get two different file times sets them
  explicitly, and one that slept 0.2 s before interrupting a run waits for the node to start. A test fails if a new
  `sleep` appears in the test suite.
- **The agflow MCP tools are documented once, and a test keeps every other document in line (F30).**
  The orchestrator prompt, `GEMINI.md`, the README and the `graph-run` skill each restated the tools' arguments, and
  they disagreed with each other and with the server (`graph_report` with or without `followups`, `graph_runs` with or
  without `workspace`). The `graph-run` skill now holds a tool reference table with every tool's exact signature; the
  other documents name the tools and point there. `tests/test_docs_sync.py` fails when the table differs from the
  tools the server registers or when any document restates an argument list differently.
- **Less duplicated code in the dashboard and in the fix engine (F29).**
  The dark colour palette is defined once (as `light-dark()` tokens) instead of twice, the rules page and the other
  component pages share one table builder, the two "undo" buttons share one function, and `fixes.py` no longer reaches
  into private names of `install.py` (`manifest_path`, `plugin_target`, `master_rule` and `BLOCK_RE` are public now).
  Toggling a rule shows the same "Disabled/Enabled" message that skills and agents already did. The dashboard now needs
  a browser from 2024 or later (`light-dark()` in CSS: Chrome/Edge 123, Firefox 120, Safari 17.5).
- **A node that lists the same dependency twice is now rejected when the graph loads (F24a).**
  `depends_on: [a, a]` used to be accepted silently. The error names the node and the repeated id; delete the repeat to
  fix a graph that has one. None of the bundled graphs does this.
- **The installer no longer says "yes" for you when there is no terminal (F13; migration).** When `easy-installer`
  ran without a terminal (a script, a pipe, CI) and without `--yes`, every question — stop agflow's processes,
  install the plugin, install uv — was silently answered yes. It now stops with "re-run with `--yes`". Pass
  `--yes` in unattended runs (CI already does). On Windows the batch file's one uv question is unchanged because
  `cmd.exe` cannot tell a missing console from an empty answer.
- **`?token=` is no longer accepted on `/api/*` (F1; migration).** Scripts that called the dashboard API with the
  token in the query string must send it as the `X-Agflow-Token` header instead. Browser use is unaffected.

### Fixed
- **The headless runner no longer leaves a node `running` when it cannot record that node's result (N9).** A finished
  task was dropped from the in-flight set before its result was written, so a full disk or a lock that timed out while
  writing left the node `running` until its lease (30 minutes by default) expired, and the run stuck behind it. The
  task now stays in flight until its result is recorded, and the existing interrupt path gives the node back without
  spending an attempt; the error itself still stops the runner.
- **agflow's hooks could never run in a real Antigravity session, and a broken agflow install turned into "Stop hook failed" (follow-up H1, found after the audit).**
  Antigravity's own documentation and its CLI log show that `hooks.json` is `{hook name: {event: …}}`, and that
  `PreToolUse`/`PostToolUse` entries must be `{matcher, hooks: [{command}]}` groups. agflow wrote
  `{"hooks": {Event: [{matcher, command}]}}`: Antigravity loaded it as a hook named `hooks`, ignored both tool-event
  entries (so the safety guard and the run ledger never ran) and ran only the flat `Stop` entry. That entry called
  `agflow hook stop`, and on a machine where the `agflow` executable's `uv tool` environment had been deleted it died
  with `ModuleNotFoundError: No module named 'agflow'` and exit code 1 — which Antigravity reports as a failed hook
  (`failed to call custom stop hook … exit status 1`) and ends the turn. Fixed in four layers:
  1. `agflow install` writes the documented layout (new `hookfile.py` reads both), with a 10-second timeout and a
     narrower PostToolUse matcher (commands, edits and subagent calls; a plain `view_file` no longer starts a process).
  2. Every installed hook command fails open: `<agflow hook …> || <note on stderr and print {}>`, exit code 0, in
     `cmd` and `sh` syntax (executed by the tests on Windows and POSIX). On Windows a path with spaces is written as
     its 8.3 short name rather than in quotes, which `cmd /c` does not unquote the way a person typing it would.
  3. The hook process cannot fail by itself: it reads its input as UTF-8 (a workspace folder with non-ASCII letters
     used to arrive as mojibake, so nothing was found and nothing recorded), always exits 0 with one JSON line, an
     answer is squeezed into the documented shapes (an unrecognised decision would block the tool call "for safety"),
     and an 8-second watchdog answers `{}` before Antigravity's timeout would turn a stuck lock into an error.
  4. `agflow doctor` and `agflow fix` run the hook commands of the global agflow plugin (only that copy, only a line
     in exactly the form the installer writes, in an empty scratch folder: a hooks.json from a cloned repository is
     never executed) the way Antigravity does and report
     `hooks.broken` (error) when it cannot start, `hooks.inert` for the old layout and `hooks.unguarded` for commands
     without fail-open; `agflow fix` reinstalls that copy, pointing the hooks at the running agflow.
- **Hooks start much faster, and do no work when no run is active (follow-up H1).**
  `python -m agflow hook …` imported `urllib.request`, `traceback`, a YAML parser and, for PostToolUse and Stop, the
  whole run store with pydantic on every call. A call with no running run now answers from one directory listing
  and the run index and imports none of them. On the development machine a hook call fell from about 0.3 s
  (PreToolUse) and 0.65 s (PostToolUse, Stop) to about 0.2 s on Windows and from 0.2 s to 0.05 s on Linux.
- **The Stop hook no longer interferes with conversations it has nothing to do with (follow-up H1).** It does not ask
  the agent to continue after an error, a limit, a timeout, a cancellation or while the agent waits for the user (the
  documented termination reasons are judged by their words), and it ignores a run that nobody has touched for 24
  hours — such a run neither pulls a new conversation back into it nor collects ledger lines from unrelated tool
  calls. A broken `guard.yaml` no longer switches off the built-in guard rules; it is reported as `guard_yaml_invalid`.
- **The orchestrator no longer spends an attempt on an outage (follow-up H1).** A subagent that died with "There was a
  network issue connecting to the server" or "Agent execution terminated due to error" did no work; the `graph-run`
  skill and the orchestrator now say to `graph_resume` and dispatch the node again instead of reporting `failed`.
- **Installing and uninstalling agflow no longer converts a Windows `GEMINI.md` from CRLF to LF line endings (found by
  sync audit S7).** Merging agflow's block into `~/.gemini/GEMINI.md` read the file in text mode, so a file with Windows
  line endings came out of `agflow install` — and out of `agflow uninstall` — with Unix ones, which shows up as every
  line changed in a version-controlled dotfile. The newline style the file already uses is now kept. The backups under
  `.agflow-backups/` stay after an uninstall, on purpose.
- **A disabled skill, agent or rule that ends up active again is now reported, and a parked item can no longer be lost (F34).**
  "Disabled" meant only "moved somewhere Antigravity does not look", with nothing on the item itself. Every parked item
  now carries a `.agflow-disabled` marker. If something moves a disabled item back into a folder Antigravity scans,
  `agflow doctor` warns (`disabled.marker_leak`) that it is active despite being disabled and says how to accept or undo
  that; a parked item whose entry in the disabled index was lost is still listed and can be enabled; and
  `disabled.in_scan_scope` warns if the parking area ever lies inside a scanned folder. Items parked by earlier versions
  have no marker and work as before.
- **`agflow sync gemini-cli` checks Gemini CLI's files before it writes anything (F35).**
  With a list or `null` where Gemini CLI's `settings.json` expects the `mcpServers` object, the sync used to crash with a
  `TypeError` after it had copied all the skills, and then treated them as foreign on the next run. It now checks the
  settings file, the skills folder and its own record first — in a dry run too — and refuses with a message that names
  the file and what was expected ("Nothing was written."). If Gemini CLI is installed, its version is shown.
- **`agflow doctor` is about 50× faster on a large plugin, and big graphs advance much faster (F27).**
  On a synthetic plugin of 350 items (200 skills with five references each) `doctor` took 65 s; it now takes 1.3 s. The
  reference check listed and tested every other skill directory for every reference; it now looks next to the skill
  first, lists the skills folder once and remembers what it has already asked the filesystem, with the same results. A
  rule file is no longer read a third time to count the always-on budget. The graph engine rendered every node's
  `outputs` again for each `when` condition it evaluated (8.1 s for 50 conditions on a 101-node graph, 0.1 s now).
- **A graph input named like a dict method (`items`, `keys`, `values`, `get`, …) now renders its value (F24a).**
  `{{ inputs.items }}` printed `<built-in method items of dict object …>` and made `when: inputs.items == '…'` always
  false, because the template engine preferred the dict's method to the input. `a.b` on a mapping now means the entry
  `b` when there is one; the method is still used when there is not. (Restricting the *names* of inputs is a separate,
  undecided change and is not part of this.)
- **A node card without a usable timeout no longer crashes the headless runner, and a timeout says which node it was (F23).**
  A hand-built card whose `timeout_min` was missing or `null` raised an unexplained `TypeError` before the agent
  started. The runner now falls back to the 30-minute default and keeps any value inside the schema's 1–1440 minute
  range; the "timed out" message reads `node '<id>' timed out after N min`.
- **A valid result from an agent is no longer thrown away because of how it is formatted (F22).**
  The headless runner located the agent's final JSON block with a pattern, so a result whose text contained `}` followed
  by a code fence, or one written as a top-level array, was reported as "no valid result JSON block" and the node
  failed. The block is now read with a JSON parser (the last valid object, or the last valid object of an array wins).
  Replies that do not end with a fenced result are still failures, with the tail of the reply in the message.
- **Antigravity's names are one versioned table you can extend, not constants spread over the code (F32).** Agent
  tool names, the SDK tool mapping, model tiers, execution policies, rule triggers, hook events, hook-payload keys
  and the workflow retirement date now live in `src/agflow/data/platform_vocab.json` (with a `vocab_version`), and
  every module derives from it. If Antigravity renames or adds one, put the change in
  `~/.gemini/config/agflow/vocab.json` (or `.agents/agflow/vocab.json`) instead of waiting for a release; an
  invalid override is ignored as a whole and reported by `agflow doctor`. Unknown tool names are reported as
  "unknown to this agflow version (vocab vX)", and a headless agent logs a warning for them instead of silently
  losing the tool. Each model tier reads its own `AGFLOW_MODEL_<TIER>` variable.
- **The dashboard no longer shows a stale page after you navigate away (F20).** If you opened a page whose data
  was slow (for example a run) and clicked another page before it answered, the late response replaced the page
  you had moved to, with no error. Each navigation now cancels the previous page's pending reads and ignores any
  result that arrives for an old navigation; the graph editor's live validation does the same for older text.
- **`agflow fix --apply` does one fewer full scan (F19).** `apply()` rebuilt the plan (a complete discovery and
  validation) even though the command had just built and shown it, then scanned again to re-check. The CLI now
  hands its plan to `apply()`, leaving the single re-check after the changes. The dashboard still re-plans on
  apply, because its preview and its click can be far apart.
- **The MCP server's run → workspace cache no longer grows without bound (F18).** Every `graph_start` /
  `graph_resume` added an entry that was never removed, in a server Antigravity keeps open for days. The cache is
  now a bounded least-recently-used map (500 entries); a run that falls out of it is still found through the
  on-disk run index.
- **The MCP server no longer parses every run on each status poll (F17).** `graph_status` (without a run id) and
  `graph_runs` validated up to 500 `state.json` files per call before keeping the newest 1 or 50, which added
  needless CPU and disk I/O to the polling loop of a long-lived server. They now read only the runs they return.
- **A malformed `mcp_config.json` gives a readable error instead of a crash (F16).** If `mcpServers` was a list or
  a string, or a server entry was not an object, `agflow mcp add/remove/enable/disable`, the dashboard's MCP page
  and `agflow profile apply` failed with an internal `TypeError`/`AttributeError`. They now stop with a message
  that names the file and what is wrong, and leave the file untouched.
- **Graph inputs that look like credentials are no longer written to run logs and reports (F11).** A secret
  passed as a graph input (for example `-i api_key=…`) was stored in plain text in `ledger.jsonl`, `report.md`
  and `report.json`. Inputs whose name looks like a credential, and well-known token shapes (`sk-…`, `ghp_…`,
  `AIza…`, `Bearer …`, private-key headers), are now replaced by `***REDACTED***` in those files. `state.json`
  keeps the real value because the engine needs it to render subagent prompts. The doctor's MCP secret check no
  longer flags names such as `AUTHOR`.
- **`agflow doctor` no longer crashes on a file that disappears or cannot be read (F9).** The GEMINI.md, MCP
  config, hooks and plugin-manifest validators (and the always-on rule size count) read files without a guard, so
  a file deleted between discovery and validation, or one that was not valid UTF-8, aborted the whole doctor run.
  Every validator now reports an `unreadable` error and carries on. An empty JSON config is treated the same way
  by all three JSON validators.
- **Backups of same-named files no longer overwrite each other (F15).** Files outside your home directory were
  backed up under their bare file name, so two `mcp_config.json` from different projects shared one backup and
  the first was lost (same-named directories were merged). They are now stored under a per-path folder, and
  backing up the same file twice keeps the first, pristine copy. Existing install manifests keep working.
- **Migrating a workflow never loses or crashes on an earlier `.bak` (F8).** `agflow migrate` / `agflow fix` kept
  the original workflow as `<name>.md.bak` by renaming onto that name: on Windows a second migration crashed with
  `FileExistsError`, on Linux/macOS it silently replaced the previous backup. They now use the first free name
  (`.bak`, `.bak2`, `.bak3`, …), identically on every platform, and the preview shows the real name.
- **Disabling a skill/agent/rule can no longer orphan it (F7).** `disable` moved the item first and recorded it in
  the index second, so a crash in between left it parked but invisible and impossible to re-enable. It now records
  first and moves second; a leftover entry from an interrupted disable is harmless and is healed by the next
  `enable`/`disable`. `agflow doctor` reports such entries (`disabled.stale`, INFO) and parked items whose files are
  gone (`disabled.lost`, WARNING).
- **`agflow install` is crash-safe (F6).** Previously the existing plugin directory was deleted before the new one
  was copied in, and the manifest — the only record of where the backup lives — was written last, so a crash or
  power loss in between left a half-installed plugin and an unreferenced backup. Now each backup is recorded in
  the manifest before the thing it describes is changed, and the new plugin is built in
  `…/agflow/install-tmp` and swapped in with two renames. `agflow install` and `agflow uninstall` first finish or
  roll back an interrupted install, and `agflow doctor` reports one as `install.interrupted`. `--link` with
  `--profile` is now refused *before* anything is touched (it used to delete the existing plugin first). The
  manifest format is unchanged.
- **A concurrent report no longer destroys a headless run (F2).** If the MCP orchestrator reported a node that
  `agflow graph run` was also working on (or the run was aborted from the dashboard meanwhile), the engine's
  "node is not running" rejection used to escape the runner, which then cancelled every in-flight node. The
  first report for a node now wins; the runner drops its late result, logs a `report_conflict` event and
  carries on. The ownership rule ("one driver per run") is documented in the graph-run skill and the README.
- **Hook latency no longer grows with the number of runs (F3).** The PostToolUse and Stop hooks (and the
  dashboard overview) used to read and validate every run's `state.json` to find the active ones. They now use a
  small self-healing index, `.agents/agflow/runs/.index.json`, so the cost depends on how many runs are *running*,
  not on how many exist (500 finished runs: 308 ms → 4.5 ms on the measurement machine). The index is a cache;
  `state.json` stays authoritative, and a damaged index is set aside as `.index.json.corrupt` and rebuilt. Older
  agflow versions ignore the new file. `agflow doctor` now reports runs whose `state.json` is unreadable
  (`runs.corrupt`) and a rebuilt index (`runs.index.corrupt`) instead of skipping them silently. The README's
  "about 70 ms per hook" claim is replaced by measured, run-count-independent numbers.
- **Discovery survives corrupt files (F5).** One bad entry in the disabled-items index (invalid JSON, unknown kind,
  missing key, non-UTF-8 bytes) or an unreadable/corrupt plugin manifest, `mcp_config.json`, `hooks.json` or
  `GEMINI.md` no longer crashes `doctor`, the dashboard or the hooks. Each problem is reported by `agflow doctor`
  as a `load` error naming the file and entry; valid neighbouring entries still load.
- **Atomic writes are now durable and identity-preserving (F14).** Every file agflow writes (configs, manifests,
  run state) is fsynced before it replaces the old one and the directory is fsynced afterwards; writing to a
  symlinked file (for example a linked dotfile) updates the link target instead of replacing the link; an
  existing file keeps its permission bits.
