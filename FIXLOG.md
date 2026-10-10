# FIXLOG — AG-Flow audit remediation

One entry per finding, updated **in the same commit as the fix** (SR3). Plan: [PLAN.md](PLAN.md). Phase 0 evidence: [docs/audit/](docs/audit/) (`phase0_verify.py` run against `bf8c178`).

Legend — **status**: `pending` · `fixed` · `n/a` (invalid / not reproduced) · `held` (needs a decision) · `ci-only` (delivered, cannot be executed here).
**Doctor rule** = the Sync-Map S10 column: the doctor check (or other machine guard) that now catches a regression, or `none` with the reason.

## Baseline (before any change, commit `bf8c178`)

| Item | Value |
|---|---|
| Platform | Windows 11, Python 3.14.0, uv 0.12.9, Node 24.15, WSL2 Ubuntu-24.04 available, `google-antigravity 0.1.20` installed |
| pytest | 173 passed, **3 failed** (all Windows test-portability, see T0) |
| Coverage | 85% (4419 stmts / 673 missed) |
| ruff | clean |
| doctor `--path plugin --strict` | 0 errors / 0 warnings |
| Hook `post_tool_use` in-process | 2.2 ms @1 run · 47.2 ms @50 · 455.4 ms @500 |
| Hook subprocess wall | 554.7 ms @1 · 606.3 ms @50 · 1010.7 ms @500 |
| Doctor, 350-item synthetic plugin | 63,365 ms |

## Test amendments (R4)

| ID | Test | Change | Justification | Commit |
|---|---|---|---|---|
| A1 | `test_dashboard.py::test_auth_and_host_checks` | query-token assertion 200 → 401 | encodes the behaviour F1 removes | pending |
| A2 | `test_easy_install.py::test_sh_half_parses[bash]` | script via stdin | baseline failure: path given to WSL launcher | T0 |
| A3 | `test_install.py::test_link_install` | skip if symlinks unsupported | baseline failure: WinError 1314 | T0 |
| A4 | `test_install.py::test_agflow_command_prefers_the_running_executable` | launcher name via `os.name` | baseline failure: POSIX name on Windows | T0 |
| A5 | `test_easy_install.py::test_find_wheel_and_requirement` | `sleep` → `os.utime` | F28 | pending |
| A6 | `test_runner.py::test_interrupt_releases_running_nodes` | `sleep` → `asyncio.Event` | F28 | pending |

## Findings

### T0 — baseline test portability
- **Status:** fixed · **Location:** `tests/test_easy_install.py`, `tests/test_install.py`, `tests/conftest.py` · **Doctor rule:** none (test-only)
- **Root cause:** three tests assumed POSIX: a path argument handed to whatever `bash` resolves to (the WSL launcher on Windows), a bare `agflow` launcher name, and unprivileged symlink creation.
- **Change:** `test_sh_half_parses` feeds the script on stdin (same syntax check, no path translation; sanity-checked that `bash -n` on stdin still rejects a broken script, exit 2); `test_agflow_command_prefers_the_running_executable` derives the launcher name from `os.name` exactly like `install.agflow_command`; `test_link_install` uses a new `require_symlinks` fixture (conftest `symlinks_supported`) that skips *with a reason* when symlinks are not permitted.
- **Tests added:** none (fixes to existing tests, amendments A2–A4).
- **Evidence (G2):** `pytest tests/test_easy_install.py tests/test_install.py` → 29 passed, 1 skipped (symlink privilege; runs in WSL/CI). `ruff check tests` clean.

### F1 — SSE token in query string
- **Status:** fixed · **Location (verified):** `dashboard/app.py:67`, `static/app.js:520` · **Doctor rule:** none (HTTP surface; guarded by `test_dashboard` + S6 audit)
- **Root cause:** `EventSource` cannot set headers; the long-lived bearer token was put in the URL (browser history, proxy and server logs).
- **Change:** the header (`X-Agflow-Token`, now compared as bytes, which also removes a `TypeError` on non-ASCII values) is the only way to present the token. New `TicketStore` (thread-safe, bounded to 256 outstanding, lazily swept) and `POST /api/stream-ticket {run_id}` (header-authenticated; 404 for an unknown run, 400 for a bad body, 429 when full) issue a `secrets.token_urlsafe(16)` ticket with a 30 s TTL. `GET /api/runs/{id}/stream?ticket=…` is the **only** place a URL credential is accepted; the ticket is consumed on first use whether or not it is valid, must match that exact run, and authenticates nothing else. `create_app` gained keyword-only `ticket_ttl`, `clock`, `stream_seconds`, `stream_poll` (defaults reproduce the old behaviour) so expiry and stream end are testable without sleeping. `app.js`: `liveStream(runId, onData)` fetches a ticket (header POST), opens `EventSource`, and on `error` closes, backs off (1 s → 15 s) and reconnects with a **new** ticket; `runDetail` uses it.
- **Files:** `src/agflow/dashboard/app.py`, `src/agflow/dashboard/static/app.js`, `README.md`, `CHANGELOG.md`, `tests/test_dashboard.py`.
- **R4 amendment A1 (applied):** `test_auth_and_host_checks` asserted `params={"token": TOKEN}` → 200; it now asserts 401. That assertion *was* the vulnerability; every other assertion in the test is unchanged.
- **Tests added (20):** query token rejected on 10 routes + the stream route; ticket requires the header token (none / wrong / query); unknown run → 404, bad body → 400; stream connects and delivers a frame; single use; expiry (fake clock); bound to its run and not a credential for other routes; wrong / empty ticket; store bounded + swept; static check that `app.js` contains no `?token=`/`&token=` and uses `stream-ticket` + `ticket=`.
- **Evidence (G1/G2/visual):** the amended test fails against the old server (200 ≠ 401). **Real-browser end-to-end** (built-in browser against a real `agflow dashboard` with a sandboxed `HOME`): the run page issues `POST /api/stream-ticket` → 200, then `GET …/stream?ticket=…` → 200, no `token=` in any URL; aborting the run from outside the page flipped the open page to `aborted` (nodes blocked, ledger entry) with no reload; `curl …/stream?token=…` → 401; killing and restarting the server made the page reconnect by itself (`stream` reset → `stream-ticket` refused while down → `stream-ticket` 200 → `stream?ticket=` 200).
- **Gotcha recorded:** Starlette's `TestClient` cannot end an endless SSE generator, hence the injectable `stream_seconds`/`stream_poll` (tests let the stream end on its own in ~0.3 s).

### F2 — concurrent MCP report destroys the headless run
- **Status:** fixed · **Location (verified):** `runner/runner.py:60` (+`:43`) · **Doctor rule:** none — the signal is a `report_conflict` event (CLI output, `ledger.jsonl`, dashboard ledger)
- **Phase-0 evidence:** `EngineError: node 'reproduce' is succeeded, not running…`, run left `running`.
- **Root cause:** `_report` is the only place a node's result is written and it assumed it was the sole reporter. `engine.report` correctly refuses a stale report, but the runner treated that refusal as fatal and its `finally` then cancelled every in-flight node.
- **Change:** `_report` catches `engine.EngineError` only, logs `{"event": "report_conflict", node, status, reason}` and returns; the run continues. A refused report changes no state (`transaction()` skips the write when its body raises). Covers both call sites (backend results and `--approve-human` auto-approval) and the "run aborted meanwhile" case. **Ownership rule** documented: whoever claims a node owns its report; the first report wins; one driver per run (graph-run SKILL, README).
- **Files:** `src/agflow/runner/runner.py`, `plugin/skills/graph-run/SKILL.md`, `README.md`, `CHANGELOG.md`, `tests/test_runner.py`.
- **Tests added (4; 3 fail on `bf8c178`, 1 is an over-catching guard):** `test_concurrent_mcp_report_does_not_kill_the_run` (the Phase-0 probe; first report wins, conflict in events *and* ledger), `test_human_auto_approve_conflict_is_tolerated`, `test_report_to_an_aborted_run_is_a_logged_conflict_not_a_crash`, `test_unrelated_exceptions_still_stop_the_run`.
- **Evidence:** see commit; Windows + WSL full suites green; `agflow doctor --path plugin --strict` 0/0.
- **Not fixed (observation N9):** if `_report` itself raises an unrelated error, the node just popped from `tasks` is not in the `finally` release list and stays `running` until its lease expires. Pre-existing, outside F2's EngineError scope.

### F3 — PostToolUse parses all runs
- **Status:** fixed · **Location (verified):** `graph/store.py:109–123`, `hooks.py:181–199` · **Doctor rules:** `runs.corrupt` (WARNING), `runs.index.corrupt` (WARNING) — *replaces the planned `runs.index.stale`: readers self-heal staleness, so only unreadable runs and a rebuilt index remain worth surfacing (PLAN §12 D-1)*
- **Phase-0 evidence:** 5.2 / 226.7 / 882.6 ms at 1 / 50 / 200 runs. Registry's "key on runs-dir mtime" is insufficient (replacing `state.json` changes the *run* directory's mtime, not `runs/`'s; and hooks are fresh processes, so in-memory caching is moot).
- **Root cause:** "which runs are active?" was answered by reading and pydantic-validating every `state.json`.
- **Change:** `RunStore` keeps `<runs>/.index.json` (`graph_id, status, created_at, updated_at` per run; additive file, older versions ignore it). `_write` stores the durable state first, then the index entry under a `FileLock`. Readers `_reconcile()` it against a **names-only** `scandir` (adds runs it has not seen, drops vanished ones, re-checks a corrupt run only if its file's mtime/size changed), then `active()` re-validates **only entries marked running** from their own `state.json` (stale "running" entries are corrected), and `list(limit=N)` parses only the newest N. A damaged index is moved aside to `.index.json.corrupt` and rebuilt. Corrupt states are flagged in the index instead of being re-parsed forever, and `RunStore.health()` feeds two new doctor warnings. `list()` without a limit keeps its exact semantics.
- **Files:** `src/agflow/graph/store.py`, `src/agflow/validate.py`, `README.md` (claim replaced), `CHANGELOG.md`, `tests/test_store_index.py` (new).
- **Tests added (19; all fail on `bf8c178` – the new API does not exist, and the behavioural ones also fail by count):** `active` equals a full scan (order included); corrupt state skipped like before + reported; repaired state picked up; index-less runs from 0.2.0 found; vanished dirs leave the index; stale "running" corrected; crash between state and index writes loses nothing; **`active()` validates only running runs (2 of 62)**; **`post_tool_use` validation count identical at 1 and 120 runs**; `list(limit)` parses only the page; limit skips corrupt and still fills the page; writers keep the index current; concurrent writers lose no entry; damaged index ×4 shapes; doctor reports / stays quiet.
- **Evidence (G1/G2/G3.4):** `scripts/bench.py --only hooks` (1 running run among N finished). **Before → after, in-process `post_tool_use`:** Windows 2.3 / 52.7 / 307.8 ms → **2.9 / 3.1 / 4.5 ms** at 1 / 50 / 500 runs; Linux (WSL2) → **0.3 / 0.4 / 1.0 ms**. Subprocess wall time is flat at ≈630 ms (Windows) / ≈210 ms (Linux) — interpreter start-up and imports (`pydantic` 166 ms, `urllib.request` 95 ms, `filelock`→`asyncio` 140 ms), unrelated to run count (observation N7). Windows 211 passed / 6 skipped; WSL 217 passed; `ruff` clean.
- **Honest limit:** the cost is proportional to the number of runs still *running* (a run abandoned without finishing or aborting stays `running` forever, and every hook then ledgers into it). That is pre-existing semantics and outside F3; logged as observation N8.

### F4 — unpinned `curl | sh`
- **Status:** fixed · **Location (verified):** `easy-installer.bat:42,87,89` · **Doctor rule:** none (installer; guarded by `test_no_pipe_to_shell_anywhere_in_the_installer`)
- **Root cause:** trust-on-first-use of whatever the CDN serves, executed through a pipe, so nothing could be verified before execution.
- **Change:** `scripts/uv-pin.env` (single source of truth: `UV_VERSION=0.12.9` + the SHA-256 of that release's `uv-installer.sh` and `uv-installer.ps1`) read by new `scripts/install-uv.sh` (dash/bash/BSD-portable: `sha256sum` → `shasum -a 256` → `openssl`; `curl --proto =https` or `wget`) and `scripts/install-uv.ps1` (`Invoke-WebRequest` + `Get-FileHash`, TLS 1.2). Both download the versioned release asset to a temp file, compare digests, print expected/actual on mismatch and **do not run** anything, and run the verified file otherwise (never piped). `AGFLOW_UV_VERSION` overrides the version but then *requires* `AGFLOW_UV_INSTALLER_SHA256`; `AGFLOW_UV_INSTALLER_URL` exists for the tests. `easy-installer.bat` calls the scripts from both halves (no labels/goto; the polyglot structure tests are unchanged).
- **Hash provenance (D4):** the two digests were read from GitHub's release metadata (`gh api repos/astral-sh/uv/releases/tags/0.12.9` → `assets[].digest`: sh `222e006c…fabc32` (71,225 bytes), ps1 `69de475b…c10a6b` (22,650 bytes)); **no installer was downloaded** to produce them. They have therefore *not* been independently re-derived from the bytes. CI's `installer` job (a clean runner without uv, real network) is the confirmation and fails loudly if a digest were wrong; I could not run it. uv 0.12.9 (released 2026-09-01) was pinned because it is the version this repository's lockfile is proven with; 0.13.0 was released the day before this work.
- **Files:** `easy-installer.bat`, `scripts/uv-pin.env` (new), `scripts/install-uv.sh` (new), `scripts/install-uv.ps1` (new), `.gitattributes` (pin file stays LF), `README.md`, `CHANGELOG.md`, `tests/test_easy_install.py`.
- **Tests added (12; all fail on `bf8c178` — the scripts do not exist and the old `.bat` pipes to a shell):** no pipe-to-shell in the `.bat` or either script; the `.bat` calls the verified scripts and no longer references `astral.sh/uv/install`; pin file well-formed and LF-only; sh bootstrap: runs only after a matching checksum / refuses a tampered installer and never runs it / uses the pinned digest by default / version override needs a checksum / unreachable download reported; the same four for PowerShell (Windows only).
- **Evidence (G1/G2):** Windows 270 passed / 6 skipped; WSL (real `dash`, `curl`) 272 passed / 4 skipped (the PowerShell tests); `ruff` clean. **End-to-end of the real `easy-installer.bat` under `cmd.exe`** in a sandbox (no uv on PATH, sandboxed profile, local fake installer, nothing downloaded): tampered → "does not match its pinned checksum, so it was NOT run", marker absent, exit 1; matching → "Checksum verified; running the installer", marker created; version override without checksum → refused with a clear message.
- **Not verified here:** a real download of the pinned installers (needs network + the CI installer job); macOS `sh` (BSD `mktemp`, `sed`, `shasum` were written to be portable, not run).

### F5 — corrupt disabled-index crashes discovery
- **Status:** fixed · **Location (verified):** `inventory.py:196–202` (+ sibling `:141`, `:152`) · **Doctor rule:** existing `load` (ERROR) now carries every one of these
- **Phase-0 evidence:** `JSONDecodeError` / `ValueError` / `KeyError` escape `Inventory.discover`.
- **Root cause:** discovery parsed agflow's *own* state (disabled index) and several plugin files with unguarded reads, while mcp/hooks readers had been guarded one by one.
- **Change:** one guarded reader `Inventory._json` / `_json_object` (JSON, UTF-8 and OS errors → `load_errors`, never raised) used by the plugin manifest, mcp, hooks and the disabled index; the index is validated as a list and **each entry is parsed on its own** (`entry #n: <error>`), so a bad entry no longer hides its valid neighbours; unreadable `GEMINI.md` is recorded (chars=0) instead of crashing. Non-object mcp/hooks/manifest JSON now reports "expected a JSON object" instead of a misleading "invalid JSON".
- **Files:** `src/agflow/inventory.py`, `tests/test_manage.py`.
- **Tests added (12; all failed on `bf8c178`):** `test_discover_survives_corrupt_disabled_index` ×6 (invalid JSON, not a list, entry not an object, unknown kind, missing key, not UTF-8), `test_valid_entries_next_to_a_bad_one_still_load`, `test_corrupt_disabled_index_is_reported_by_doctor`, `test_discover_survives_corrupt_plugin_manifest`, `test_discover_survives_non_utf8_plugin_files` ×3.
- **Evidence (G1/G2):** 187 passed, 1 skipped (was 175+1); `ruff` clean.
- **Follow-up in F9:** `validate_gemini_md` must skip an item already reported unreadable here.

### F6 — non-atomic plugin switch
- **Status:** fixed · **Location (verified):** `install.py:167–204` · **Doctor rule:** `install.interrupted` (WARNING; manual guidance added in `fixes._manual`)
- **Root cause:** the destructive step (remove the old plugin) preceded the only durable record of its backup (the manifest, written last), and the new bundle was built *in place* after the removal.
- **Change (journal pattern: back up → record → act):** `record_write()` now persists the manifest immediately after taking each backup and before the caller changes anything (and `managed_blocks` is appended *before* it, so a persisted manifest is always self-consistent). The plugin bundle is built in `<state>/install-tmp/<hex>-new` (outside every directory Antigravity scans), then swapped with two renames (live → `<hex>-old`, new → live); a failed second rename puts the old plugin back, a failed first rename raises `InstallError` ("files … probably in use") with the old plugin untouched, and a failed deletion of the superseded copy becomes a plan warning, not a failure. New public `recover_interrupted(scope, workspace, dry_run)` is run first by `install()` and `uninstall()`: a `*-old` with no live plugin is put back, a `*-old` next to a live one is deleted, a `*-new` is deleted. `--link` + `--profile` is refused up front (it used to delete the plugin first). New `paths.install_tmp_dir()`. Manifest schema unchanged (R2).
- **Files:** `src/agflow/install.py`, `src/agflow/paths.py`, `src/agflow/validate.py`, `src/agflow/fixes.py`, `README.md`, `CHANGELOG.md`, `tests/test_install_atomic.py` (new), `tests/conftest.py` (sandbox guard, see Incident I-1).
- **Tests added (23):** crash-at-every-step matrix ×5 points (stage build, between the two renames, deleting the old copy, GEMINI.md write, final manifest write) × 2 (recover+uninstall restores the original tree; re-install after the crash completes it), manifest-on-disk-before-the-old-plugin-is-touched, failure while building leaves the live plugin alone, locked directory keeps the old plugin and explains, failed move-in puts the old plugin back, `--link --profile` refused before anything changes, recover no-op / puts back a moved-aside plugin, doctor reports `install.interrupted` until recovered, debris never discovered as a second plugin, install→uninstall leaves the tree identical (global ×2 profiles, workspace ×2 profiles).
- **Evidence (G1):** the core regression run against the *old* `install.py` → `{'manifest_exists': False, 'old_plugin_untouched': False}` (plugin already deleted, no manifest), against the new → both `True`. **G2:** Windows 238 passed / 6 skipped; WSL Ubuntu 244 passed / 0 skipped; `ruff` clean; `doctor --path plugin --strict` 0/0/0.
- **Parity (R7):** rename-of-directories exercised on Windows and POSIX. The Windows file-lock path is simulated (a `PermissionError` on the first rename); a genuinely locked directory was not provoked.
- **Pre-existing, logged (N10):** `install`/`uninstall` rewrite a user's `GEMINI.md` with LF line endings even if it was CRLF (the merge reads with universal newlines and writes `newline="\n"`), so a Windows user's CRLF `GEMINI.md` is not restored byte-for-byte by an install→uninstall round trip. Outside F6.

### Incident I-1 — a test run modified the developer's real `~/.gemini` (found and repaired during F6)
- **What happened:** while writing the F6 crash tests I used `monkeypatch.undo()`, which also reverted the autouse fixture that sandboxes `$HOME`. The following `install("global")` calls (5 parametrised runs of one test) therefore ran against the **real** home and replaced the developer's global agflow plugin with a copy built from this worktree; the real plugin's `mcp_config.json` and the install manifest pointed at the worktree's `.venv\Scripts\agflow.exe`. The real home had a global agflow install beforehand (Antigravity's own `config.json`, written the day before, had `plugins/agflow/enabled: true`; `agflow v0.2.0` is installed as a uv tool).
- **Detected:** the tests failed on a doctor assertion that printed the real `C:\Users\…\.gemini\config\skills\…` path; a read-only inspection of `~/.gemini` (timestamps, `install.json`, `GEMINI.md`, `config.json`) confirmed it.
- **Repaired (08:11):** re-ran `agflow install` from the developer's own installed tool (`~/.local/bin/agflow.exe`, v0.2.0) after a `--dry-run`. Verified: plugin bundle from the installed wheel; MCP command `C:\Users\Koosha Kazino\.local\bin\agflow.exe`; manifest `source`/`command` no longer reference the worktree; same two `created` entries as before; exactly one managed block in `GEMINI.md`; Antigravity's `config.json` untouched (still enabled); `agflow doctor --source plugin:agflow` 0/0/0. The exact pre-incident bytes of the plugin files could not be recovered (no backup existed because the first-touch rule had nothing to back up), but the result is what a fresh `agflow install` of the installed v0.2.0 produces. Antigravity should be restarted to pick the plugin up again.
- **Prevented from recurring:** (1) all `monkeypatch.undo()` calls in the test-suite replaced by scoped `monkeypatch.context()`; (2) a **session-scoped sandbox guard** in `tests/conftest.py` wraps `paths.home()` and raises `RuntimeError("test escaped its sandbox …")` if HOME ever resolves outside pytest's temp tree — verified with a deliberately escaping test that it fails loudly instead of touching the real home. The guard is installed with its own `MonkeyPatch`, so a test cannot undo it.

### F7 — non-atomic disable
- **Status:** fixed · **Location (verified):** `toggles.py:59–63` · **Doctor rules:** `disabled.stale` (INFO: entry whose parked copy is gone but the item is still in place), `disabled.lost` (WARNING: neither the parked copy nor the original exists)
- **Phase-0 evidence:** crash between move and index write → item parked, invisible to the inventory, impossible to re-enable.
- **Root cause:** the move (first durable effect) preceded the index write, so a crash orphaned the parked item.
- **Change:** `disable()` is journal-first: the index entry is written (atomic, F14) *before* the move; a failed move (`Exception`) takes the entry back out; a hard stop between the two leaves an entry whose parked copy does not exist while the item is still in place — harmless. `enable()` heals exactly that case (drops the stale entry and returns the original path); `disable()` removes stale duplicates of the same item before appending; "both copies exist" still refuses to overwrite. `Inventory` now records index entries with no parked copy (`stale_disabled`) so doctor can report them instead of skipping silently.
- **Files:** `src/agflow/toggles.py`, `src/agflow/inventory.py`, `src/agflow/validate.py`, `CHANGELOG.md`, `tests/test_manage.py`.
- **Tests added (6; 4 fail on `bf8c178`):** crash after the journal / before the move keeps the item enabled and a retry does not duplicate the entry; a failing journal write moves nothing; a failed move rolls the entry back; `enable` heals a stale entry; `enable` still refuses when both copies exist; doctor reports stale and lost entries.
- **Evidence:** Windows 276 passed / 6 skipped; WSL 278 passed / 4 skipped; `ruff` clean.
- **Note:** a crash model detail — the rollback is `except Exception` (not `BaseException`) so that a hard stop (or Ctrl+C) leaves the harmless entry instead of racing a half-done rollback; the tests simulate the hard stop with a `BaseException`. Orphans created by *older* versions are recovered by F34's marker files.

### F8 — `.bak` rename
- **Status:** fixed · **Location (verified):** `migrate.py:66`, twin `fixes.py:593` · **Doctor rule:** none (migration helper; guarded by the tests below)
- **Phase-0 evidence (Windows):** `FileExistsError` when `ship.md.bak` exists; on POSIX `rename(2)` silently replaces it.
- **Root cause:** a fixed backup name combined with a rename whose overwrite behaviour differs per platform.
- **Change:** `fsutil.unique_sibling(path, suffix=".bak")` returns the first free of `x.bak`, `x.bak2`, `x.bak3`, … (a dangling symlink counts as taken). `migrate_workflows` and `fixes._fix_workflow` — the two places that rename a converted workflow — both use it, so the preview (`report.actions`, fix `changes`) and the real run name the *actual* destination, in dry-run too.
- **Files:** `src/agflow/fsutil.py`, `src/agflow/migrate.py`, `src/agflow/fixes.py`, `CHANGELOG.md`, `tests/test_fsutil.py`, `tests/test_manage.py`, `tests/test_fixes.py`.
- **Tests added (6; 5 fail on `bf8c178`, the 6th needs symlink privilege):** `unique_sibling` sequence; dangling symlink counts as taken; migrate keeps an existing `.bak` and uses `.bak2`; migrating three rounds yields `.bak/.bak2/.bak3` with the right contents; the dry run announces the name the real run uses; the workflow *fix* keeps an earlier `.bak`, previews the real destination and `fix --undo` removes `.bak2` and restores the workflow.
- **Evidence:** Windows (where the old code crashed) and WSL (where it silently overwrote) both green for the three test files; `ruff` clean.

### F9 — unguarded reads crash doctor
- **Status:** fixed · **Location (verified):** `validate.py:69,216,245,271` (+`:94`,`:314`) · **Doctor rule:** existing `unreadable` (ERROR) now covers every validator
- **Phase-0 evidence:** deleting the file between discovery and validation → `FileNotFoundError` in `validate_gemini_md`, `validate_mcp_file`, `validate_hooks_file`, `validate_plugin_manifest`. An empty `plugin.json` was *not* a crash (error `plugin.json`); only an inconsistency with mcp/hooks.
- **Root cause:** reading was not centralised — `_load` guarded, six other call sites did not.
- **Change:** `_read_text(path)` is the one way validators read a file (missing / unreadable / not UTF-8 → an `unreadable` ERROR, processing continues); `_load_json(path, code)` parses JSON on top of it and treats an empty or whitespace-only file as `{}` for **all three** JSON validators alike; `_load` accepts pre-read text so `validate_rule` reads its file once (the second read at `:94` is gone) and always-on counting in `validate_inventory` goes through `_read_text` (`:314`); `legacy_config` also tolerates non-UTF-8 files. Discovery marks an unreadable `GEMINI.md` (`meta.unreadable`) and `validate_inventory` skips it, so it is reported once (as the `load` error from F5) instead of twice.
- **Files:** `src/agflow/validate.py`, `src/agflow/inventory.py`, `CHANGELOG.md`, `tests/test_validate.py`.
- **Tests added (17 failed on `bf8c178`):** 7 validators × {missing, binary, directory, empty} never crash and say `unreadable` (28 parametrized cases); empty JSON files are treated alike; whole-inventory validation survives files deleted after discovery; an unreadable `GEMINI.md` is reported exactly once.
- **Note for F27:** rule files are still read twice (discovery + validation); F27 reduces that.

### F10 — legacy migration backup coverage
- **Status:** **n/a — not reproduced** · **Location (verified):** `fixes.py:608–618` · **Doctor rule:** n/a
- **Evidence:** legacy workspace (`rules`,`skills`,`workflows`) + legacy global (`skills`,`global_workflows`,`mcp_config.json`) → `fix` applied 2 fixes and changed the tree; `fix --undo` restored it **byte-identical** (`docs/audit/phase0_verify_output.txt`, F10). `_fix_legacy` touches exactly the paths `migrate_legacy_workspace/global` mutate; the audit's "missing" paths (`rules_dir`, `workflows_dirs`, `mcp_file` of the other scope) are never written by those functions.
- **Action:** no production change. `test_legacy_migration_fix_is_fully_undoable_workspace_and_global` locks the invariant the finding was worried about (it passes on `bf8c178`, as expected for a not-reproduced finding). Out of scope, logged as N3: `agflow migrate` / `POST /api/migrate` move and rename without a transaction by design.

### F11 — graph inputs unredacted in ledger
- **Status:** fixed · **Location (verified):** `graph/store.py:52` (+ the same sink class: `render_report_md` inputs section and `report.json` via `status_view`) · **Doctor rule:** none (artifact content; guarded by `tests/test_redact.py`)
- **Phase-0 evidence:** `"inputs": {"api_key": "sk-live-SECRET123"}` verbatim in `ledger.jsonl`.
- **Root cause:** the run-artifact writers serialised `inputs` as given; there was no shared notion of "secret-looking" outside the doctor's MCP check.
- **Change:** new stdlib-only `agflow/redact.py`: `REDACTED`, `SECRET_KEY_RE` (moved from `validate.py`, which imports it — one definition; `AUTH` now skips author/authority but still matches Authorization, so doctor's MCP secret check no longer flags `AUTHOR`-style names), `redact_text()` (well-known token shapes: `sk-`, `gh[pousr]_`, `github_pat_`, `AIza`, `xox*-`, `AKIA…`, `Bearer …`, PEM private-key headers) and `redact_mapping()` (deep, non-mutating, idempotent; a secret-looking *key* hides its whole value, empty/None/False/0 are left alone). Applied to the `run_started` ledger event, the Inputs section of `report.md` and `report.json`. **`state.json` keeps the real values on purpose** — the engine renders `{{ inputs.* }}` into subagent prompts from it (tested); documented in the README.
- **Files:** `src/agflow/redact.py` (new), `src/agflow/graph/store.py`, `src/agflow/validate.py`, `README.md`, `CHANGELOG.md`, `tests/test_redact.py` (new).
- **Tests added (33; all fail on `bf8c178` — the module does not exist, and the run-folder tests fail by content):** 10 secret-looking keys; 7 ordinary keys stay; nesting + no mutation; structured value hidden by a secret key; 8 token shapes under an innocent key; empty/non-string untouched; idempotent; `validate` re-exports the shared pattern; the ledger never holds the secret; reports never hold it while `state.json` and the rendered prompt still do; the secret appears in no file of the run folder except `state.json`.
- **Limit:** only inputs and shapes listed above are recognised; a free-text secret in an agent's summary under an unremarkable key is not detected.

### F12 — force-kill matches by loose CommandLine
- **Status:** pending · **Location (verified):** `scripts/easy_install.py:151–164,219–229` · **Doctor rule:** none (installer script)
- **Change / files / tests / evidence:** pending.

### F13 — `confirm()` auto-approves without tty
- **Status:** pending · **Location (verified):** `scripts/easy_install.py:289–293` (+ sh half `easy-installer.bat:79`) · **Doctor rule:** none
- **Change / files / tests / evidence:** pending.

### F14 — atomic write hazards
- **Status:** fixed · **Location (verified):** `fsutil.py:17–27` · **Doctor rule:** none (primitive; guarded by `tests/test_fsutil.py`)
- **Phase-0 evidence:** no `fsync` in module; symlink/mode not exercisable on this Windows machine (WinError 1314) → proven in WSL below.
- **Root cause:** the helper was a rename recipe without the durability (fsync file + directory) and identity-preservation (symlink, mode) steps.
- **Change:** `atomic_write_text(path, text, *, durable=True)`: writes *through* a symlink (link survives, target replaced; a dangling link creates its target); preserves an existing file's permission bits on POSIX (new files stay `0600`, deliberately); fsyncs the file before `os.replace` and the parent directory after it (POSIX; only the "filesystem cannot fsync a directory" errnos `EINVAL/ENOTSUP/EBADF` are tolerated, everything else propagates). `write_json`/`write_yaml` inherit it.
- **Files:** `src/agflow/fsutil.py`, `tests/test_fsutil.py` (new), `tests/conftest.py` (from T0).
- **Tests added (10):** fsync-before-rename ordering, directory fsync, `durable=False`, symlink stays a symlink, dangling symlink, mode preserved, new-file mode unchanged, failed replace leaves original and no temp file, parent dirs created, json/yaml writers inherit.
- **Evidence (G1, POSIX):** against the *old* `fsutil.py` in WSL, **7 of 10 fail** (symlink replaced by a regular file; mode became 0600; no fsync; no `durable` kwarg) and **10/10 pass** with the fix. **G2:** Windows 192 passed / 6 skipped (POSIX-only + symlink privilege); WSL Ubuntu-24.04 (Python 3.12.3) **198 passed, 0 skipped**; `ruff` clean.
- **Parity (R7):** Windows native + POSIX (WSL) both exercised. Windows has no directory fsync (cannot open a directory) and ignores mode bits by design.

### F15 — backup collision
- **Status:** fixed · **Location (verified):** `fsutil.py:78–92` · **Doctor rule:** none (primitive; guarded by `tests/test_fsutil.py`)
- **Phase-0 evidence:** two `mcp_config.json` from different dirs mapped to one destination (`'from b'`, first lost); two `plugin/` dirs merged into one backup.
- **Root cause:** outside the anchor the destination was the bare file name, and `dirs_exist_ok=True` merged instead of failing.
- **Change:** `backup()` places anything outside the anchor at `_external/<sha1(resolved path)[:12]>/<name>`; no more `dirs_exist_ok`; if the same path was already backed up into the same root, the first (pristine) copy is kept and returned. Anchor-relative layout is unchanged. Manifests store absolute backup paths, so manifests written by 0.2.0 still uninstall (tested).
- **Files:** `src/agflow/fsutil.py`, `tests/test_fsutil.py`, `tests/test_install_atomic.py`.
- **Tests added (6):** same-name files from different dirs both restorable; same-name dirs not merged; same path twice keeps the pristine copy; anchor-relative layout unchanged; missing path returns None; a 0.2.0-style manifest still uninstalls.
- **Evidence:** Windows 287 passed / 7 skipped; WSL (POSIX) 290 passed / 4 skipped; `ruff` clean.

### F16 — `mcpServers` type validation
- **Status:** fixed · **Location (verified):** `mcp_registry.py:45–50,96–104` · **Doctor rule:** none (CLI/API error path; doctor already validates the file's shape via `mcp.schema`/`mcp.server`)
- **Phase-0 evidence:** `mcpServers: []` + `add` → `TypeError`; a scalar entry + `set_enabled` → `TypeError`; invalid JSON → `JSONDecodeError` — none were `McpError`.
- **Root cause:** `_load` checked only that the top level was an object.
- **Change:** `_load` now rejects, with `McpError` naming the file: unreadable / non-UTF-8 / invalid JSON, a non-object top level, a non-object `mcpServers`, and any non-object server entry (listing which). A refused operation never touches the file. `toggles.apply_profile` translates `McpError` into `ToggleError`, which the CLI and the dashboard already handle (previously a broken config surfaced as a raw error or HTTP 500).
- **Files:** `src/agflow/mcp_registry.py`, `src/agflow/toggles.py`, `CHANGELOG.md`, `tests/test_manage.py`.
- **Tests added (30; 24 fail on `bf8c178`):** 7 malformed shapes × {list, add, remove, toggle} each raise `McpError` naming the file and leave the file byte-identical; dashboard add/toggle/remove answer 400; applying a profile over a broken config raises `ToggleError`.

### F17 — `graph_status`/`graph_runs` full parse
- **Status:** fixed · **Location (verified):** `mcp_server.py:166,180` · **Doctor rule:** none
- **Root cause:** both tools called `store.list()`, which reads and validates every `state.json` (up to the 500-run cap) and only then sliced `[:50]` / took `[0]`; the documented orchestration loop polls `graph_status` regularly.
- **Change (depends on F3):** `graph_status` without a `run_id` uses `store.list(limit=1)`; `graph_runs` uses `store.list(limit=50)` (the status index picks the newest candidates; only those are parsed) or `store.active()[:50]` for `only_active`. Responses are unchanged in shape and order.
- **Files:** `src/agflow/mcp_server.py`, `CHANGELOG.md`, `tests/test_mcp_server.py`.
- **Tests added (2; 1 fails on `bf8c178`):** with 60 finished + 1 running runs a `graph_runs` page parses exactly 50 (old: 61), `only_active` parses 1, `graph_status` with no id parses 1; `graph_status` with no runs still says so.

### F18 — `_workspaces` leak
- **Status:** pending · **Location (verified):** `mcp_server.py:35,105,198` · **Doctor rule:** none
- **Change / files / tests / evidence:** pending.

### F19 — `apply()` double discovery
- **Status:** pending · **Location (verified):** `fixes.py:728,749` · **Doctor rule:** none
- **Change / files / tests / evidence:** pending.

### F20 — stale-render race
- **Status:** pending · **Location (verified):** `static/app.js:532–545,448–455` · **Doctor rule:** none
- **Change / files / tests / evidence:** pending.

### F21 — `SdkBackend.run` untested
- **Status:** pending · **Location (verified):** `runner/backends.py:179–199` · **Doctor rule:** none
- **Change / files / tests / evidence:** pending.

### F22 — fragile JSON-extraction regex
- **Status:** pending · **Location (verified):** `runner/backends.py:45` · **Doctor rule:** none
- **Phase-0 note:** registry trigger corrected — ``` inside a string alone parses; `} ```` inside a string and top-level arrays fail.
- **Change / files / tests / evidence:** pending.

### F23 — `timeout_min=None`
- **Status:** pending · **Location (verified):** `runner/backends.py:197` · **Doctor rule:** none
- **Change / files / tests / evidence:** pending.

### F24 — duplicate `depends_on`, free-form `inputs` keys
- **Status:** pending (F24a) / **held** (F24b, decision D1) · **Location (verified):** `graph/schema.py:69,110` · **Doctor rule:** planned `graph.input.name` (F24b)
- **Change / files / tests / evidence:** pending.

### F25 — dead `innerHTML` sink
- **Status:** pending · **Location (verified):** `static/app.js:35` · **Doctor rule:** none (static test)
- **Change / files / tests / evidence:** pending.

### F26 — SDK policy ordering undocumented
- **Status:** pending · **Location (verified):** `runner/backends.py:163` · **Doctor rule:** none (contract test)
- **Evidence:** SDK documents `Specific Deny > … > Wildcard Allow, first match wins` (`google/antigravity/hooks/policy.py:19–24`).
- **Change / files / tests / evidence:** pending.

### F27 — performance drains
- **Status:** pending · **Location (verified):** `validate.py:150–156` (+reads `:53,:94,:314`), `graph/engine.py:152–193` · **Doctor rule:** none
- **Phase-0 evidence:** doctor 63,365 ms on a 350-item plugin; one rule file read 4×.
- **Change / files / tests / evidence:** pending.

### F28 — coverage gate and timing patterns
- **Status:** pending · **Location (verified):** `pyproject.toml`, `.github/workflows/ci.yml`, `test_easy_install.py:65`, `test_runner.py:89` · **Doctor rule:** none
- **Change / files / tests / evidence:** pending.

### F29 — frontend duplication / install privates
- **Status:** pending · **Location (verified):** `fixes.py:380,382,419,420,463,466,496,498`; `static/app.js:205,291,325–358`; `static/app.css:25–38` · **Doctor rule:** none
- **Change / files / tests / evidence:** pending.

### F30 — documentation drift
- **Status:** pending · **Location (verified):** `plugin/GEMINI.md:57,61`, `agents/orchestrator.md:39`, `skills/graph-run/SKILL.md:21,31` · **Doctor rule:** none — pytest guard `test_docs_sync.py` (repo-internal docs, not user config)
- **Change / files / tests / evidence:** pending.

### F31 — hook guard fails silently
- **Status:** pending · **Location (verified):** `hooks.py:4,32–35,160–175,242–257` · **Doctor rule:** planned `hooks.guard_blind`
- **Phase-0 evidence:** `rm -rf /` in an unrecognised payload shape → `{}`, no log, no signal.
- **Change / files / tests / evidence:** pending. **Note:** payload fixtures will be labelled `captured` / `synthetic` honestly.

### F32 — platform vocabulary
- **Status:** pending · **Location (verified):** `validate.py:20–33,326–328`, `runner/backends.py:90–98,202–208`, `fixes.py:351` · **Doctor rule:** planned `vocab.override.invalid`
- **Change / files / tests / evidence:** pending.

### F33 — SDK contract tests
- **Status:** pending / `ci-only` (live run) · **Location (verified):** `tests/test_sdk_backend.py`, `runner/backends.py:125–146` · **Doctor rule:** none
- **Phase-0 note:** drifted — existing tests already use the real SDK when installed; missing parts are the snapshot, the live run and the schedule.
- **Change / files / tests / evidence:** pending.

### F34 — toggles rely on negative-space discovery
- **Status:** pending · **Location (verified):** `toggles.py:1–3`, `paths.py:89–93` · **Doctor rule:** planned `disabled.marker_leak`, `disabled.in_scan_scope`
- **Change / files / tests / evidence:** pending.

### F35 — sync to Gemini CLI unprobed
- **Status:** pending · **Location (verified):** `sync.py:34–83`, `paths.py:182–191` · **Doctor rule:** none (command-time probe raises `SyncError`)
- **Change / files / tests / evidence:** pending.

## Phase gates

### Phase 1 (P0: F5, F14, F3, F2, F6, F1, F4) — G2
| Check | Result |
|---|---|
| `pytest -q`, Windows 11 / Python 3.14 | **270 passed, 6 skipped** (POSIX-only + symlink privilege) |
| `pytest -q`, WSL Ubuntu-24.04 / Python 3.12 (POSIX parity, offline venv) | **272 passed, 4 skipped** (PowerShell-only) |
| `pytest -q`, Windows / Python 3.11 (3.10 is not installed locally; static scan for 3.11+-only APIs found none; ruff target py310 clean) | **270 passed, 6 skipped** |
| Coverage | **86%** (baseline 85%; floor 84%) |
| `ruff check src tests scripts` | clean |
| `agflow doctor --path plugin --strict` | 0 errors / 0 warnings / 0 info |
| `agflow doctor` on an installed, realistic sandboxed workspace (user skill + rule + MCP + graph + global install) | 0 / 0 / 0 |
| Real `~/.gemini` after the Incident I-1 repair | untouched since 08:11 (install.json, GEMINI.md timestamps) |
| Sync audits S1–S10 | not yet automated (Phase 4). Manual sweep for this phase: S6 `stream-ticket` ↔ `app.js` consistent; S7 install→uninstall round-trip covered by 6 tests; S9 README updated for hooks, install, dashboard auth, run ownership |
