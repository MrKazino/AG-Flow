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
| A2 | `test_easy_install.py::test_sh_half_parses[bash]` | script via stdin | baseline failure: path given to WSL launcher | pending |
| A3 | `test_install.py::test_link_install` | skip if symlinks unsupported | baseline failure: WinError 1314 | pending |
| A4 | `test_install.py::test_agflow_command_prefers_the_running_executable` | launcher name via `os.name` | baseline failure: POSIX name on Windows | pending |
| A5 | `test_easy_install.py::test_find_wheel_and_requirement` | `sleep` → `os.utime` | F28 | pending |
| A6 | `test_runner.py::test_interrupt_releases_running_nodes` | `sleep` → `asyncio.Event` | F28 | pending |

## Findings

### T0 — baseline test portability
- **Status:** pending · **Location:** `tests/test_easy_install.py`, `tests/test_install.py`, `tests/conftest.py`
- **Root cause:** tests assume POSIX paths/launcher names/symlink privilege. **Change / files / tests / evidence:** pending. **Doctor rule:** none (test-only).

### F1 — SSE token in query string
- **Status:** pending · **Location (verified):** `dashboard/app.py:67`, `static/app.js:520` · **Doctor rule:** none (HTTP surface; guarded by `test_dashboard`)
- **Root cause:** `EventSource` cannot set headers; the bearer token was put in the URL.
- **Change / files / tests / evidence:** pending.

### F2 — concurrent MCP report destroys the headless run
- **Status:** pending · **Location (verified):** `runner/runner.py:60` (+`:43`) · **Doctor rule:** none (event `report_conflict`)
- **Phase-0 evidence:** `EngineError: node 'reproduce' is succeeded, not running…`, run left `running`.
- **Change / files / tests / evidence:** pending.

### F3 — PostToolUse parses all runs
- **Status:** pending · **Location (verified):** `graph/store.py:109–123`, `hooks.py:181–199` · **Doctor rule:** planned `runs.index.stale`
- **Phase-0 evidence:** 5.2 / 226.7 / 882.6 ms at 1 / 50 / 200 runs. Registry's "key on runs-dir mtime" is insufficient (see PLAN F3).
- **Change / files / tests / evidence:** pending.

### F4 — unpinned `curl | sh`
- **Status:** pending · **Location (verified):** `easy-installer.bat:42,87,89` · **Doctor rule:** none (installer; guarded by `test_no_pipe_to_shell_in_installer`)
- **Change / files / tests / evidence:** pending.

### F5 — corrupt disabled-index crashes discovery
- **Status:** pending · **Location (verified):** `inventory.py:196–202` (+ sibling `:141`, `:152`) · **Doctor rule:** existing `load`
- **Phase-0 evidence:** `JSONDecodeError` / `ValueError` / `KeyError` escape `Inventory.discover`.
- **Change / files / tests / evidence:** pending.

### F6 — non-atomic plugin switch
- **Status:** pending · **Location (verified):** `install.py:167–204` · **Doctor rule:** planned `install.interrupted`
- **Change / files / tests / evidence:** pending.

### F7 — non-atomic disable
- **Status:** pending · **Location (verified):** `toggles.py:59–63` · **Doctor rule:** planned `disabled.stale`
- **Phase-0 evidence:** crash between move and index write → item parked, invisible.
- **Change / files / tests / evidence:** pending.

### F8 — `.bak` rename
- **Status:** pending · **Location (verified):** `migrate.py:66`, twin `fixes.py:593` · **Doctor rule:** none (migration helper)
- **Phase-0 evidence (Windows):** `FileExistsError` when `.bak` exists.
- **Change / files / tests / evidence:** pending.

### F9 — unguarded reads crash doctor
- **Status:** pending · **Location (verified):** `validate.py:69,216,245,271` (+`:94`,`:314`) · **Doctor rule:** existing `unreadable`
- **Phase-0 note:** empty `plugin.json` is *not* a crash (yields error `plugin.json`); consistency fix only.
- **Change / files / tests / evidence:** pending.

### F10 — legacy migration backup coverage
- **Status:** **n/a — not reproduced** · **Location (verified):** `fixes.py:608–618` · **Doctor rule:** n/a
- **Evidence:** legacy workspace (`rules`,`skills`,`workflows`) + legacy global (`skills`,`global_workflows`,`mcp_config.json`) → `fix` applied 2 fixes and changed the tree; `fix --undo` restored it **byte-identical** (`docs/audit/phase0_verify_output.txt`, F10). `_fix_legacy` touches exactly the paths `migrate_legacy_workspace/global` mutate; the audit's "missing" paths are never written by those functions.
- **Action:** no production change; one characterization test planned to lock the invariant.

### F11 — graph inputs unredacted in ledger
- **Status:** pending · **Location (verified):** `graph/store.py:52` (+ report.md/report.json sinks) · **Doctor rule:** none (artifact content)
- **Phase-0 evidence:** `"inputs": {"api_key": "sk-live-SECRET123"}` in `ledger.jsonl`.
- **Change / files / tests / evidence:** pending.

### F12 — force-kill matches by loose CommandLine
- **Status:** pending · **Location (verified):** `scripts/easy_install.py:151–164,219–229` · **Doctor rule:** none (installer script)
- **Change / files / tests / evidence:** pending.

### F13 — `confirm()` auto-approves without tty
- **Status:** pending · **Location (verified):** `scripts/easy_install.py:289–293` (+ sh half `easy-installer.bat:79`) · **Doctor rule:** none
- **Change / files / tests / evidence:** pending.

### F14 — atomic write hazards
- **Status:** pending · **Location (verified):** `fsutil.py:17–27` · **Doctor rule:** none (primitive)
- **Phase-0 evidence:** no `fsync` in module; symlink/mode not exercisable on this Windows machine (WinError 1314) → to be proven in WSL.
- **Change / files / tests / evidence:** pending.

### F15 — backup collision
- **Status:** pending · **Location (verified):** `fsutil.py:78–92` · **Doctor rule:** none (primitive)
- **Phase-0 evidence:** two `mcp_config.json` → one destination (`'from b'`); two `plugin/` dirs merged.
- **Change / files / tests / evidence:** pending.

### F16 — `mcpServers` type validation
- **Status:** pending · **Location (verified):** `mcp_registry.py:45–50,96–104` · **Doctor rule:** none (CLI/API error path)
- **Change / files / tests / evidence:** pending.

### F17 — `graph_status`/`graph_runs` full parse
- **Status:** pending · **Location (verified):** `mcp_server.py:166,180` · **Doctor rule:** none
- **Change / files / tests / evidence:** pending.

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
