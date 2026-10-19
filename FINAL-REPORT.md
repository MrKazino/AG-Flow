# FINAL REPORT — AG-Flow audit remediation (v0.2.0 → v0.3.0)

Branch `ag-flow-super-intelligence-si5028` (base `bf8c178`), worktree `.workflows/ag-flow-super-intelligence-si5028`.
Deliverables: `PLAN.md`, `FIXLOG.md`, `CHANGELOG.md` (Unreleased), this report, and `COMMIT-DRAFTS.md` (see "Commits").

```
EXECUTION SUMMARY
  Findings closed: 35/35 — 35 fixed (F24b as a warning, decision D1 taken at its default), F10 not reproduced (locked by a test)
  Version: 0.3.0 prepared (code, metadata, lock file, changelog, release notes); NOT committed, tagged or released — yours to do
  Commits: 18 made on the branch (not pushed), 31 more drafted in COMMIT-DRAFTS.md and NOT made, at the owner's request
  Follow-up H1 (after the audit): hooks.json was in a layout Antigravity ignores (only Stop ran) and a stale agflow.exe
         made that hook fail the agent; fixed, with every hook command now failing open (see FIXLOG H1)
  Tests: 173 passed + 3 failed at the baseline  →  759 passed / 7 skipped (Windows, Python 3.14 and 3.11),
         760 passed / 6 skipped (Linux, Python 3.12);  after H1 and the 0.3.0 leftovers: 893 passed / 7 skipped on both Windows 3.14 and Linux 3.12
  Coverage: 85% → 89% (Windows and Linux); CI floor 84% → 88%
  Doctor: 0 errors / 0 warnings / 0 info on the bundled plugin (--strict) and on a fresh sandboxed install (G3.1)
  Speed: doctor on a 350-item plugin 63.4 s → 1.3 s; one engine pass over 50 conditions 8.1 s → 0.1 s;
         in-process PostToolUse hook 455 ms → ≈ 4 ms at 500 runs;
         a hook process (H1): PostToolUse/Stop 650 → 195 ms and PreToolUse 296 → 188 ms on Windows, ≈ 0.2 → 0.05 s on Linux
  Sync audits S1–S10: 34 checks, clean twice in a row and once more at the end
```

## Read this first

* **Nothing has been committed or pushed since F17.** You asked to commit and push under your own name, so F18–F35 and
  Phase 4 are in the working tree. `COMMIT-DRAFTS.md` has one message per change (no co-author trailer) and the files each
  touches. Several findings touch the same files (`app.js`, `validate.py`, `backends.py`, `cli.py`, `hooks.py`,
  `FIXLOG.md`, `CHANGELOG.md`, `README.md`), so following the drafts one by one means splitting hunks with `git add -p`.
  If that is too fiddly, a few coarse commits work as well (for example by phase: hooks and vocabulary; runner and graph;
  dashboard; doctor performance; tests and CI; sync audits) — every intermediate state of the *whole* tree is green, a
  partial one has not been run.
* **The 18 commits that exist are yours alone.** Their messages were rewritten to carry no co-author line (same files,
  authors and dates; only the message changed, so their hashes are new). They are local and unpushed.
* **Never run on a real runner:** the new CI jobs (Windows and macOS matrix, coverage floor) and the nightly
  `sdk-contract.yml` with its live step. **Never run at all:** the SDK backend with a real model.
* **No decision of the plan is open any more.** D1 (F24b) was taken at the plan's own default — a doctor warning, an error in 0.4.0 — and D5 became **0.3.0**;
  both can be reversed in a line each. `COMMIT-DRAFTS.md` ends with the exact release steps (the last commit, the `v0.3.0` tag, `gh release create`),
  and `RELEASE-NOTES-0.3.0.md` is the body for the GitHub release. None of them has been run.
* **Follow-up H1 changes what you see in Antigravity, and one repair is waiting for your yes.** Antigravity's own log shows that it
  loaded agflow's `hooks.json` as a hook named `hooks` and ran only its `Stop` entry, which failed (`ModuleNotFoundError: No module
  named 'agflow'`, exit 1) because the `agflow.exe` in `~/.local/bin` has lost its tool environment. The file is now in the documented
  layout and every hook command fails open, so after `agflow install` the PreToolUse guard **starts to run in every session** (it asks
  before a force-push, `git reset --hard`, reading `.env`…; `--no-hooks` or `AGFLOW_GUARD=off` opts out). **The agflow already installed on
  this machine was not touched** (it lives outside the repository): `agflow doctor` cannot even start with it. Repair: run
  `easy-installer.bat` from this tree (it reinstalls the uv tool and the plugin), or `uv tool install --force --reinstall` on this folder
  followed by `agflow install`. It is not done yet, because it changes your real `~/.gemini` and tool environment.
  The "network issue" and most "Agent execution terminated due to error" messages in your history are Antigravity failing to reach
  Google's model service (about 95 % of 2,139 error events in the transcripts on this machine) and are not caused by agflow.

## Phase log

**Phase 0 — discovery and plan.** Re-verified the registry against the code on a baseline (173 tests passing and 3
failing for Windows-portability reasons, 85% coverage, doctor 63 s on a 350-item plugin). **33 findings were confirmed,
one could not be reproduced (F10) and one had drifted (F33: the real SDK was already used when installed; what was
missing was the contract tests and a nightly job).** Four were confirmed with a correction: F22's stated trigger was wrong
(a fence inside a JSON string does not break the old pattern; a `}` followed by a fence, and a top-level array, do), F9
had more unguarded reads than listed, F3 needed a different design than the one proposed (a self-healing on-disk index),
and F24b would break user-written graphs, so it was split off and held. `PLAN.md`: verification matrix, impact map,
dependency graph, 35 task cards, the S1–S10 audits, gates, the R2/R4 ledgers and decisions D1–D5. Gate: self-review
against the rules.

**Follow-up H1 (after Phase 5).** Looking at what a real session logs: hooks that never ran, a hook that failed the agent when its
executable broke, and hook processes that spent 0.3–0.65 s on imports. Documented layout, fail-open commands, a hook process that
cannot fail by itself (UTF-8 input, strict answers, watchdog), an idle fast path, a Stop that does not push on after an error, and a
doctor probe that proves the installed hooks start (limited to the global copy and to installer-shaped lines after a security review
of its first version). Details, evidence and the tests: `FIXLOG.md` H1, `PLAN.md` D-15.

**Phase 1 — P0 (F5, F14, F3, F2, F6, F1, F4).** Corrupt-file-tolerant discovery, durable and symlink-safe atomic writes,
an index that makes the hot hook path independent of the run count, a concurrent MCP report that no longer kills a
headless run, a crash-safe plugin install (journal, two-rename swap, `recover_interrupted`), a one-time stream ticket in
place of the SSE query token, and a pinned, checksum-verified uv bootstrap. Gate G2: 270 passed on Windows, 272 on Linux,
86% coverage. One incident (**I-1**): a test undid the `$HOME` sandbox and overwrote the real `~/.gemini`
(`install("global")` ×5); it was repaired by re-running the install from the installed tool, documented in `FIXLOG.md`, and
prevented from recurring (scoped `monkeypatch.context()`, a session-wide sandbox guard that fails a test that
resolves `$HOME` outside pytest's temp tree).

**Phase 2 — P1/P2 (F7, F8, F15, F9, F10, F11, F16, F17, F18, F19, F12, F13, F20, then F32 → F31).** Journalled
enable/disable, collision-free `.bak` names and backups, one guarded file reader, credential redaction in ledgers and
reports, `mcpServers` shape validation, bounded caches and polls, a process matcher that only stops agflow's own
processes, an installer that no longer consents for you, a latest-wins guard for the dashboard. **F32** put Antigravity's
names into one versioned, user-overridable table; **F31**, built on it, found that Antigravity documents a hook payload
that agflow's guard did not read (`rm -rf /` in the documented shape was *allowed*) — see "Remaining risks".

**Phase 3 — P3 (F22, F23, F24a, F25, F26, F27, F29, F30, F28, F21, F35, F34, F33).** The result parser, the timeout
helper, graph validation and template lookup, the HTML sink, the guard-precedence lock, the doctor and engine performance
work, the duplicated code, the documentation drift, CI and coverage, `SdkBackend.run` tests, the sync probe, the
disabled-item markers, and the SDK contract tests with a nightly job. Gate: 759/760 tests, 89% coverage, doctor 1.3 s.

**Phase 4 — sync audits.** `tests/test_sync_audits.py` (marker `sync_audit`): ten audits, 34 checks, clean twice in a
row. They found and led to fixes for: path literals outside `paths.py` (S1), a ledger without a registry (S2), a
hard-coded tool name (S4), CRLF snapshot files written on Windows (S8), a missing README command (S9) and — a real
defect — **a Windows `GEMINI.md` converted from CRLF to LF by install and uninstall** (S7, observation N10).

**Phase 5 — G3.** Fresh-copy install, doctor, dashboard with stream ticket and a headless run in a sandbox (17/17), with
the real environment fingerprinted before and after (identical); parity on Windows 3.14 / 3.11 and Linux 3.12;
performance at 1 / 50 / 500 runs; a security re-check. Details and what G3 did *not* cover are in `FIXLOG.md`.

## Per-finding table

| F# | Severity | Status | Commit | Tests added | Doctor rule | Evidence |
|---|---|---|---|---|---|---|
| F1 | high | fixed | c9754cd | 20 | none | query token → 401 on every route; one-time stream ticket verified in a real browser and in G3.1 |
| F2 | high | fixed | c8acfc9 | 4 | none | a concurrent MCP report no longer kills the run (`report_conflict` event); 3 tests fail on bf8c178 |
| F3 | high | fixed | 17412bd | 19 | `runs.corrupt`, `runs.index.corrupt` | PostToolUse 455 ms → ≈ 4 ms at 500 runs through a self-healing index; 19 tests |
| F4 | high | fixed | cf7165e | 12 | none | uv bootstrap pinned and SHA-256-verified before it runs; a tampered file is refused and nothing runs |
| F5 | high | fixed | 9230bca | 12 | `load` (existing, now complete) | a corrupt disabled-index entry or plugin file no longer crashes discovery; 12 tests failed on bf8c178 |
| F6 | high | fixed | f93a386 | 23 | `install.interrupted` | a crash at every step of install leaves a recoverable state; atomic two-rename swap; `install.interrupted` |
| F7 | medium | fixed | 037accc | 6 | `disabled.stale`, `disabled.lost` | journal-then-move disable: every crash point leaves the item enabled or parked and indexed |
| F8 | medium | fixed | 5be1e23 | 6 | none | a `.bak` collision picks a free name (no FileExistsError, no silent overwrite) |
| F9 | medium | fixed | eff76b5 | 17 | `unreadable` (existing, now complete) | one guarded reader; 17 tests that crashed doctor on bf8c178 now report `unreadable` |
| F10 | medium | not reproduced | b096578 | 1 (the lock) | n/a | not reproduced on bf8c178; the undo invariant is locked by a test |
| F11 | medium | fixed | 809ce24 | 33 | none | api_key-like graph inputs show `***REDACTED***` in the ledger and reports; state.json unchanged |
| F12 | medium | fixed | draft §F12 | 13 | none | process match by path (and creation time); only agflow's own processes, only those listed; live WMI check |
| F13 | medium | fixed | draft §F13 | 9 | none | no terminal and no `--yes` → abort (sh half and Python); the batch half's limit is documented |
| F14 | medium (raised to P0) | fixed | bd75225 | 10 | none | fsync before rename and of the directory, symlink write-through, file mode kept |
| F15 | medium | fixed | 0b584ec | 6 | none | same-name backups from different places no longer collide |
| F16 | medium | fixed | d51709c | 30 | none | a malformed `mcpServers` → `McpError` instead of `TypeError` (30 tests) |
| F17 | medium | fixed | d6ede04 | 2 | none | status polls read only the runs they return |
| F18 | medium | fixed | draft §F18 | 2 | none | the workspace cache is an LRU capped at the index cap |
| F19 | medium | fixed | draft §F19 | 3 | none | `apply(plan=…)` does one discovery instead of two (CLI flow: 2 in total, was 3) |
| F20 | medium | fixed | draft §F20 | 11 | none | latest-wins guard; stale route renders dropped (real-browser reproduction before and after) |
| F21 | low | fixed | draft §F21 | 34 cases in 16 test functions | none | `SdkBackend.run` tested without the SDK or a model; backends.py coverage 79 % → 99 % |
| F22 | low | fixed | draft §F22 | 15 cases in 7 test functions | none | a valid result containing `} ```` in a string, or written as an array, no longer fails the node |
| F23 | low | fixed | draft §F23 | 19 cases (now in `tests/test_sdk_run.py`) | none | a card without a timeout runs; the timeout message names the node |
| F24 | low | F24a fixed; F24b held (D1) | draft §F24 | 12 cases in 5 test functions | `graph.input.name` planned for F24b, not added | F24a: a repeated `depends_on` is rejected, `inputs.items` renders the input. F24b held for D1 |
| F25 | low | fixed | draft §F25 | 3 | none | no HTML or code sink in any dashboard script; scan test |
| F26 | low | fixed | draft §F26 | 5 cases in 4 test functions | none | the guard's deny outranks `allow_all()` in both orders, against the installed SDK (a lock: passes on old code) |
| F27 | low | fixed | draft §F27 | 88 cases in 7 test functions | none | doctor 65 s → 1.3 s on 350 items; engine pass 8.1 s → 0.1 s |
| F28 | low | fixed; CI never run on real runners | draft §F28 | 4 | none | pytest-cov, CI floor 88 %, Windows and macOS jobs, no sleeping tests (CI not run on real runners) |
| F29 | low | fixed | draft §F29 | 5 | none | one palette, one table builder, one undo, public install helpers |
| F30 | low | fixed | draft §F30 | 12 cases in 4 test functions | none | MCP signatures specified once; every other document tested against the live server |
| F31 | medium (raised to high) | fixed | draft §F31 | 35 contract tests + 1 static JS test | `hooks.guard_blind` | the documented hook payload is understood; a blind guard raises banner, doctor warning and ledger event |
| F32 | medium | fixed | draft §F32 | 28 | `vocab.override`, `vocab.override.invalid` | one versioned, overridable platform vocabulary, golden-tested |
| F33 | low | fixed locally; nightly CI delivered, never run | draft §F33 | 18 cases in 15 test functions | none | SDK surface snapshot and contract tests; nightly workflow delivered, never run |
| F34 | low | fixed | draft §F34 | 27 cases in 19 test functions | `disabled.marker_leak`, `disabled.in_scan_scope` | markers make a leaked or orphaned disabled item visible and recoverable |
| F35 | low | fixed | draft §F35 | 23 cases in 14 test functions | none | sync probes Gemini CLI's files first and refuses with a clear message, writing nothing |

"Commit" shows the hash of the commit that exists, or `draft §Fn` for a change that is only drafted in `COMMIT-DRAFTS.md`.
F10 has a commit because its test-only lock was committed; F24's draft covers F24a. The doctor-rule column repeats
FIXLOG's cell: where it says "none", the reason is in FIXLOG (an API/CLI error path, a primitive guarded by its own
tests, a pytest guard because the invariant concerns the repository's own documents rather than a user's configuration).

## Sync audit results (`pytest -m sync_audit`: 34 passed, twice in a row, and once more after the last change)

| # | Result | Notes |
|---|---|---|
| S1 | pass | Antigravity's paths are named only in `paths.py`. Found and routed through `paths.plugin_layout` / `paths.agy_cli_mcp_file`: `install.py`, `fixes.py`, `mcp_registry.py`, `scripts/bench.py`. The scan looks at path construction (not every `"skills"` string: those are also dict keys). Allow-list: empty |
| S2 | pass | every `RunState`/`NodeState` field survives a round trip through the store; the 20 ledger events are registered in `graph.store.LEDGER_EVENTS` (the registry did not exist) and all are still written; MCP table = live server |
| S3 | pass | hook payload fixtures classify as declared and are listed in their README; the README names only payload keys the vocabulary knows |
| S4 | pass | no tool, model or hook-event name outside `platform_vocab.py` (one in `bench.py` fixed). Rule triggers and execution policies are behaviour agflow defines, not Google's identifiers: allowed in code, required to stay in the vocabulary |
| S5 | pass | the `graph-run` table is the only place the MCP signatures are spelled out |
| S6 | pass | every call in `app.js` has a route; `GET /api/fix/history` is called by nothing (allow-listed with a reason: API for scripts) |
| S7 | pass | **found a defect:** install → uninstall turned a CRLF `GEMINI.md` into LF. Fixed. Round trip is byte-identical for global and workspace scope, with and without a profile, LF and CRLF (the backups under `.agflow-backups/` stay on purpose) |
| S8 | pass | the report-key and SDK snapshots regenerate byte-identically (both had been written with CRLF on Windows: the writers now write LF) |
| S9 | pass | README counts, Python requirement, coverage floor (now 88) and command list equal the repository (`agflow version` was missing; `hook` is allow-listed) |
| S10 | pass | FIXLOG has all 35 findings, each with a doctor-rule cell, none pending |

## Remaining risks and next steps

**Decisions for you**

1. **D1 — F24b (graph input names): taken at the plan's default.** A doctor warning (`graph.input.name`) now; an error in 0.4.0, which the
   CHANGELOG announces. Nothing that loaded before stops loading (a test pins it). Hyphenated ids get an INFO hint (N4). If you would rather
   reject them in 0.3.0, say so: it is the same one-line check made an error, and it breaks graphs that use `Feature` or `bugID`.
2. **D5 — version: 0.3.0.** `src/agflow/__init__.py`, `pyproject.toml`, `plugin/plugin.json`, `uv.lock`, the README example and the CHANGELOG
   heading (`[0.3.0] — 2026-10-16`, with an empty `[Unreleased]` above it) carry it; `uv lock --check` passes and the built wheel reports 0.3.0.
   The repository URL in the metadata and the README now follows `origin` (`MrKazino/AG-Flow`, it said `google-antigravity-workflow`).
3. **D-12 — browser requirement.** If an older browser must stay supported, F29's palette change is a one-file revert.
4. **The 18 existing commits' co-author line**: removed from the messages; nothing further to do.
5. **The agflow installed on the development machine is still the broken one** (its tool environment lost the `agflow` package). Repairing it changes your
   real tool directory and `~/.gemini`, so it waits for your yes (`easy-installer.bat` from this tree, or the commands in `RELEASE-NOTES-0.3.0.md`).

**Not verified, said plainly**

* **GitHub Actions:** the Windows/macOS test matrix, the coverage floor (88%) and `sdk-contract.yml` have never run. The
  floor is the Linux / Python 3.12 measurement (89.35%) minus one; the Python 3.10 job is also unmeasured here
  (Python 3.10 is not installed on this machine; 3.11 and 3.14 on Windows and 3.12 on Linux pass).
* **The nightly live step** needs the `GOOGLE_API_KEY` repository secret (the SDK reads `GEMINI_API_KEY`; the workflow
  maps one to the other — found in the SDK's own tests, not tried). Without the secret it warns and passes on schedule,
  fails on a manual run. Whether a real agent finishes the smoke graph, and whether its `report.json` has exactly the
  committed key set, is what the first live run will show.
* **The pinned uv installer digests (F4)** came from GitHub's release metadata, as agreed (D4); no installer was
  downloaded to confirm them. The CI `installer` job, which runs the real installer on three systems, is the check.
* **G3.1** ran the real installer logic in a sandbox against a copy of the working tree. It did not run the `.bat` half
  under `cmd.exe` end to end (its last step edits your real `PATH`), did not download uv, and used the fake backend for
  the headless run.
* **N11 — do Antigravity's hooks run at all? Answered by H1: no, except `Stop`.** The CLI log shows one named hook `hooks` loaded and
  only its `Stop` handler run; the layout was corrected. Still **not seen live**: a `deny` or a ledger line produced by a hook in the
  Antigravity app (no live session was possible here), so the claim that the guard now runs in the app rests on the documentation, the
  CLI's log of how it loads the file and the engine's own strings (FIXLOG H1, "Not verified"). After reinstalling, one run with
  `AGFLOW_HOOK_DEBUG=1` shows payloads in `~/.gemini/config/agflow/hook-debug.jsonl`; they can be added to
  `tests/fixtures/hook_payloads/` as `captured` samples (none exist yet).

**Observations outside the registry — status for 0.3.0**

N1 an unwritable global run index was invisible — **fixed (H3)**: a `run_index_unwritable` ledger event. N2 `hooks._debug_log` swallows
`OSError` — **kept on purpose** (a hook must not fail on its debug file; FIXLOG). N3 `agflow migrate` and `POST /api/migrate` had no undo —
**fixed (H4)**: one batch in the `agflow fix` history. N4 hyphens in input ids — **hint added** (`graph.input.hyphen`, README, schema). N5 the README's
old "about 70 ms per hook" was not reproducible; the README says what was measured and that yours will differ. N6 the SDK has no wheels on some
platforms — nothing to change (the installer falls back and says so). N7 hook start-up (≈ 0.6 s on Windows, ≈ 0.2 s on Linux) — **reduced by H1** to
≈ 0.2 s and ≈ 0.05 s. N8 a run abandoned without finishing stays `running` — **H1 stops hooks from ledgering into it or nagging about it after 24
hours**; the run itself still needs `graph_abort`. N9 a node left `running` when its result cannot be recorded — **fixed (H2)**. N10 — closed by S7.
N11 — answered by H1.

**Smaller facts worth knowing**

* The in-process hook benchmark on this machine is bimodal (≈ 3–4 ms or ≈ 9–13 ms in repeated runs, at every size and in no
  order of size): "no growth with the run count" is the claim, not the absolute number.
* `uv.lock` was re-resolved when `pytest-cov` was added; uv also rewrote three dependency markers (`h11`, `truststore`,
  `typing-extensions`) to equivalent forms. `uv lock --check` passes.
* `find_workspace()` walks up from the current directory to the nearest folder with `.git` or `.agents`; run from a
  temporary folder under a profile that has its own `~/.agents`, it takes that profile as the workspace. This is how the
  first G3.1 attempt showed 22 skills instead of 20 (two of the owner's own); it is existing behaviour, left as is.
* Throughout, the scratch tooling (the offline Linux venv for the POSIX runs, the dashboard sandbox server, the helper
  scripts) lived outside the repository and is not a deliverable; results are quoted with the platform they ran on.
