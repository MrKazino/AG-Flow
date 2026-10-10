# AG-Flow audit remediation — master plan

> Phase 0 deliverable (rule R1). No production code was written to produce this document.
> Branch `claude/ag-flow-audit-remediation-ec9028`, base commit `bf8c178` (v0.2.0).
> Every claim marked **CONFIRMED** below was reproduced by running code against the repository, not just by reading it.
> The probe script and raw output are summarised in §3; baselines are in §2.

## Contents

1. [Executive summary](#1-executive-summary)
2. [Baseline and environment](#2-baseline-and-environment)
3. [Verification matrix (Phase 0, step 1)](#3-verification-matrix)
4. [Impact map (Phase 0, step 2)](#4-impact-map)
5. [Execution order (DAG)](#5-execution-order-dag)
6. [Task cards](#6-task-cards)
7. [Sync audits and the per-task sync checklist](#7-sync-audits)
8. [Verification gates per finding](#8-verification-gates-per-finding)
9. [R2 / R4 ledgers, holds and approvals](#9-r2--r4-ledgers-holds-and-approvals)
10. [Self-review against R1–R10](#10-self-review-against-r1r10)
11. [Observations outside the registry](#11-observations-outside-the-registry)

---

## 1. Executive summary

**Outcome of re-verification.** 33 of the 35 findings reproduce as described. Two need correcting before any work starts:

| Finding | Verdict | Consequence |
|---|---|---|
| **F10** | **NOT REPRODUCED.** `fix` of a legacy workspace *and* a legacy global tree, followed by `fix --undo`, restores both trees byte-for-byte. `_fix_legacy` touches exactly the paths `migrate_legacy_workspace` / `migrate_legacy_global` mutate (the audit's "missing `rules_dir` / `workflows_dirs` / `mcp_file`" are paths those functions never write). | Recorded **N/A with evidence**. No fix is fabricated. One characterization test is added to lock the invariant (the finding's actual concern). |
| **F33** | **PARTIALLY DRIFTED.** `tests/test_sdk_backend.py` uses the *real* SDK when it is installed (it is, in this environment: `google-antigravity 0.1.20`), not stubs. What is genuinely missing: a signature/enum snapshot, a live `run()` check, and a scheduled job. | Scope narrowed to what is missing. The snapshot part is verifiable locally; the live-run part is CI-only and will be labelled so. |
| **F22** | **CONFIRMED, but the stated trigger is wrong.** Triple-backticks inside a JSON string do *not* break parsing. What does: a `}` followed by a fence inside a string value, and top-level arrays. | Fix is unchanged; the regression tests use the corrected triggers. |
| **F9** | **CONFIRMED** (4 crashers + 2 unguarded second reads at `validate.py:94,314`). The "empty-file" part is *not* a crash: an empty `plugin.json` already yields a `plugin.json` error. | Empty-file handling becomes a consistency fix, not a crash fix. |

Three more observations change how the work is sequenced:

* **The real SDK is installed here**, so F26 (policy precedence) and the snapshot half of F33 can be verified against the real package. The SDK documents its own rule: *"Specific Deny > Specific Ask > Specific Allow > Wildcard Deny > Wildcard Ask > Wildcard Allow; within a group, first match wins"* (`google/antigravity/hooks/policy.py:19-24`). `policy.allow_all()` is a wildcard allow, so it can never outrank agflow's specific `run_command` deny, in either list order. Locked by test, not by hope.
* **The baseline test run on this Windows machine is 173 passed, 3 failed** (not "171 passed"). All three are test-portability defects, not product bugs (§2, task **T0**).
* **F3's mtime-on-the-runs-directory fix would not work.** `os.replace` of `state.json` changes the *run* directory's mtime, not the parent `runs/` directory's. Also, a hook is a fresh process per call, so any in-memory cache is useless. The plan therefore uses a small **on-disk index** with self-healing (§6, F3).

**What gets built.** 35 registry tasks + 1 baseline-portability task (T0) + 3 deliverable-only tasks (sync-audit tests, bench script, docs). F10 resolves to a test-only closure.

**Execution order** (full DAG in §5; this follows the prescribed phase order, with the dependency edges the prompt calls out plus the ones found during discovery):

```
T0  →  Phase 1: F5 → F14 → F3 → F2 → F6 → F1 → F4
    →  Phase 2: F7, F8, F15, F9, F10(test only), F11, F16, F17, F18, F19, F12, F13, F20, F32 → F31
    →  Phase 3: F22, F23, F24, F25, F26, F27, F29, F30, F28, F21, F35, F34, F33
    →  Phase 4: S1–S10 audits ×2 clean   →   Phase 5: G3, FINAL-REPORT.md
```

**Size.** ≈ 45–50 atomic commits. Estimated change ≈ **5,000–6,500 lines** (about 55% tests and docs). Per-task estimates are on each card.

**Decisions I need from you (everything else proceeds on documented defaults).**

| # | Decision | Default I will use | When it blocks |
|---|---|---|---|
| D1 | **F24b — restrict graph `inputs` keys to `ID_RE`.** Rejecting existing keys such as `Feature` or `bugID` in user-authored graphs is a breaking change to a user-authored on-disk format (R2). | Ship F24a now (reject duplicate `depends_on`, fix `{{ inputs.items }}` resolution at the root cause). For `ID_RE`: **warn-now / error-in-0.4.0** (doctor `graph.input.name` WARNING). | Phase 3, only F24b. |
| D2 | **F13 — non-interactive installs without `--yes` now abort** instead of auto-approving. This is registry-mandated, but scripts that piped answers will need `--yes`. | Implement as specified; CHANGELOG "Changed" + migration note. | Never (proceeds). |
| D3 | **F1 — `?token=` is no longer accepted on `/api/*`.** Registry-mandated. One existing test asserts the vulnerable behaviour (R4 ledger entry A1). | Implement as specified. | Never (proceeds). |
| D4 | **F4 — pinned uv installer hashes.** I will read SHA-256 digests from GitHub's release metadata (`gh api`, no file download). An extra confidence check would download the installer once. | Metadata only. Ask before any download. | Phase 1, F4. |
| D5 | **Version bump.** | Propose **0.3.0** (user-visible: dashboard stream auth, non-tty installer abort, vocab override file, new `.index.json`, schema strictness). | Phase 5. |

---

## 2. Baseline and environment

Measured at the start of this session on the audit commit, **before any change**.

| Item | Value |
|---|---|
| Platform | Windows 11 (10.0.26300), Python 3.14.0 (uv-managed), uv 0.12.9, Node v24.15.0, git 2.55, WSL2 `Ubuntu-24.04` present (stopped) |
| Real SDK | `google-antigravity 0.1.20` **installed** in the venv (contradicts the "may not be installed" assumption; imports stay lazy regardless) |
| `pytest -q` | **173 passed, 3 failed** (audit baseline said 171 passed; the 2-test difference is platform/skip drift) |
| Coverage (`pytest-cov`, run ad hoc) | **85%** total (4419 stmts, 673 missed). Weakest: `__main__.py` 0%, `cli.py` 67%, `backends.py` 79%, `dashboard/app.py` 81%, `hooks.py` 83%, `sync.py` 83%, `mcp_server.py` 84% |
| `ruff check src tests scripts` | clean |
| `agflow doctor --path plugin --strict` | 0 errors, 0 warnings, 0 info |
| `agflow doctor` (real home) | 0 errors, 4 warnings, 39 info (user environment, unrelated to this repo) |

**The 3 baseline failures (all test-portability defects):**

| Test | Cause (evidence) |
|---|---|
| `test_easy_install.py::test_sh_half_parses[bash]` | `shutil.which("bash")` resolves to the **WSL launcher**; it is handed a Windows path and fails with `/bin/bash: C:\Users\…\easy-installer.bat: No such file or directory`, exit 127. The test is path-dependent, not syntax-dependent. |
| `test_install.py::test_link_install` | `OSError: [WinError 1314] A required privilege is not held by the client` — creating a symlink needs Developer Mode/admin on Windows. |
| `test_install.py::test_agflow_command_prefers_the_running_executable` | Hard-codes the POSIX launcher name `agflow`; on Windows the launcher is `agflow.exe`, and the production code (`install.agflow_command`) correctly returns it. |

**Performance baselines** (`bench.py`, this machine, median; the doctor number is the headline):

| Measure | 1 run | 50 runs | 200 / 500 runs |
|---|---|---|---|
| `post_tool_use` in-process (F3) | 2.2 ms | 47.2 ms | 455.4 ms @ 500 (≈ 0.9 ms **per run**, linear) |
| `agflow hook post-tool-use` subprocess wall time | 554.7 ms | 606.3 ms | 1010.7 ms @ 500 |
| `agflow doctor` on a 350-item synthetic plugin (200 skills × 5 refs, 50 agents, 100 rules) (F27) | **63,365 ms** | | |

The subprocess figure also shows that the README's "about 70 ms" hook claim does not hold on this machine even at 1 run (Python start-up + imports dominate). The README will be corrected to a measured, run-count-independent statement (S9).

**Layout drift from the prompt's §2** (recorded so nobody greps the wrong path):

| Prompt says | Actual |
|---|---|
| `src/agflow/engine.py` | `src/agflow/graph/engine.py` |
| `plugin/feature-delivery.yaml` | `plugin/graphs/feature-delivery.yaml` (5 bundled graphs) |
| `plugin/hooks.json` | present at `plugin/hooks.json` ✓ |
| Audit line anchors | **No drift**: HEAD is still `bf8c178`, so every anchor is exact. Ranges the audit gave loosely are tightened in §3. |

**Not available / not safe to do from here:** pushing a branch or running GitHub Actions (outward-facing, not authorised). CI-config changes (F28 matrix, F33 nightly) are delivered but unverified on real runners and will be reported as such.

---

## 3. Verification matrix

`F# | verdict | actual location | evidence`. "Probe" = executed against the code in an isolated `$HOME`.

| F# | Verdict | Actual location | Evidence |
|---|---|---|---|
| F1 | CONFIRMED | `dashboard/app.py:67`, `static/app.js:520` | Server reads `request.query_params.get("token")`; client builds `/stream?token=…`. `tests/test_dashboard.py:25` asserts the query token returns 200. |
| F2 | CONFIRMED | `runner/runner.py:60` (`_report` 75–80); same exposure at `:43` (auto-approve) | Probe: backend reports the node through the store mid-flight → `run_graph` raised `EngineError: node 'reproduce' is succeeded, not running…`; run left `running`. |
| F3 | CONFIRMED | `graph/store.py:109–123`; `hooks.py:181–199` (and `hooks.stop`, `dashboard/app.py:89`) | Probe: `post_tool_use` = 5.2 ms @1 run, 226.7 ms @50, 882.6 ms @200. Linear. |
| F4 | CONFIRMED | `easy-installer.bat:42` (`irm … \| iex`), `:87` (`curl … \| sh`), `:89` (`wget … \| sh`) | Read: no version in URL, no hash. |
| F5 | CONFIRMED | `inventory.py:196–202` | Probe: bad JSON → `JSONDecodeError`; bad kind → `ValueError`; missing key → `KeyError`; all escape `Inventory.discover`. **Sibling, same root cause (F5b):** corrupt `plugin.json` → `JSONDecodeError` at `inventory.py:141`; unguarded `gemini_md.read_text` at `:152`. |
| F6 | CONFIRMED | `install.py:167–204` (remove at `:172`, manifest at `:204`) | Read: `record_write(target)` only mutates the in-memory manifest; first durable write is the last statement. |
| F7 | CONFIRMED | `toggles.py:59–63` | Probe: crash injected between move and index write → skill dir gone, parked copy exists, **not visible** in inventory. |
| F8 | CONFIRMED | `migrate.py:66` (twin at `fixes.py:593`) | Probe on Windows: `FileExistsError` when `ship.md.bak` exists. POSIX overwrite follows from `rename(2)` semantics. |
| F9 | CONFIRMED (+ scope note) | `validate.py:69, 216, 245, 271`; extra: `:94`, `:314` | Probe: deleting the file → `FileNotFoundError` in 4 validators. Empty `plugin.json` → error code `plugin.json`, no crash. |
| F10 | **NOT REPRODUCED** | `fixes.py:608–618` | Probe: legacy workspace (`rules`, `skills`, `workflows`) + legacy global (`skills`, `global_workflows`, `mcp_config.json`) → `fix` applied 2 fixes, tree changed, `fix --undo` → **tree identical**. |
| F11 | CONFIRMED | `graph/store.py:52`; same sink class: `render_report_md` (`:133–134`) and `report.json` (`write_report`) | Probe: `api_key=sk-live-SECRET123` appears verbatim in `ledger.jsonl`. |
| F12 | CONFIRMED | `scripts/easy_install.py:151–164` (clause `:160`), `:219–229` | Read: `CommandLine.IndexOf($t)` matches any process mentioning the path; no identity re-check before `taskkill`. |
| F13 | CONFIRMED | `scripts/easy_install.py:289–293`; **same pattern** in `easy-installer.bat:79` (sh half: prompt only `[ -t 0 ]`, else installs silently) | Read. |
| F14 | CONFIRMED | `fsutil.py:17–27` | Probe: no `fsync` anywhere in the module. Symlink and mode behaviour: **cannot be exercised on this machine** (WinError 1314); follows from `os.replace(tmp, link)`/`mkstemp(0600)` semantics, to be proven on WSL (§8). |
| F15 | CONFIRMED | `fsutil.py:78–92` | Probe: two `mcp_config.json` from different dirs → same destination, content `'from b'` (first lost). Directories: two different `plugin/` dirs **merged** into one backup. |
| F16 | CONFIRMED | `mcp_registry.py:45–50, 96–104` | Probe: `mcpServers: []`+`add` → `TypeError`; scalar entry + `set_enabled` → `TypeError`; invalid JSON → `JSONDecodeError` (none are `McpError`). |
| F17 | CONFIRMED | `mcp_server.py:166, 180` | Read: `store.list()` (full pydantic parse of every run) then `[:50]`. |
| F18 | CONFIRMED | `mcp_server.py:35, 105, 198` | 2 insert sites, 0 eviction sites. |
| F19 | CONFIRMED | `fixes.py:728, 749` | Probe: 2 `Inventory.discover` calls inside one `apply()` (3 when the caller already built a plan to display). |
| F20 | CONFIRMED | `app.js:532–545` (`route`), `:448–455` (`validate`) | 0 sequence/Abort guards in `app.js`. |
| F21 | CONFIRMED | `runner/backends.py:179–199` | `SdkBackend.run` is not exercised by any test. |
| F22 | CONFIRMED (trigger corrected) | `runner/backends.py:45` | Probe: `"…} ``` …"` inside a string → classified *failed* ("no valid result JSON block") although the JSON is valid; top-level array → *failed*. ``` inside a string alone → parses fine. |
| F23 | CONFIRMED (narrow) | `runner/backends.py:197` | `None * 60` → `TypeError: unsupported operand`. Reachable only with a hand-built card (`engine.build_card` always supplies an int). |
| F24 | CONFIRMED | `graph/schema.py:69, 110` | Probe: `depends_on: [a, a]` accepted; input named `items` accepted and `{{ inputs.items }}` renders `<built-in method items of dict …>`. |
| F25 | CONFIRMED | `app.js:35` | `innerHTML` present; 0 callers pass `html:`. |
| F26 | CONFIRMED | `runner/backends.py:163` | SDK `_policy_sort_key` orders specific-deny before wildcard-allow regardless of list order (probe: `['d','allow_all']` for both orders). Correct today, but unlocked in agflow. |
| F27 | CONFIRMED (severe) | `validate.py:150–156`; reads at `:53, :94, :314`; `graph/engine.py:152–193` | One always-on rule file is read **4×** across discover+validate. **`doctor` takes 63 s on a 350-item plugin** (§2). |
| F28 | CONFIRMED | `pyproject.toml` dev group; CI `uv run pytest -q`; `test_easy_install.py:65`; `test_runner.py:89` | `pytest-cov` absent; no `--cov` in CI. |
| F29 | CONFIRMED | `fixes.py:380,382,419,420,463,466,496,498` (privates `_manifest_path/_plugin_target/_BLOCK_RE/_master_rule`); `app.js:205,291` (undo twice), `:325–358` (rules page); `app.css:25-38` (dark palette twice) | Probe output. |
| F30 | CONFIRMED | `plugin/GEMINI.md:57,61`; `plugin/agents/orchestrator.md:39`; `skills/graph-run/SKILL.md:21,31` | Probe: `graph_report(…, artifacts)` vs `(…, artifacts, followups)`; `graph_runs(only_active=true)` vs `graph_runs(workspace, only_active=true)`. Also repeated in `rules/orchestration.md`, `prompts/orchestrate.md`, `README.md:127–138`. |
| F31 | CONFIRMED | `hooks.py:4, 32–35, 160–175, 242–257` | Probe: `rm -rf /` inside an unrecognised payload shape → stdout `{}`, **no debug log, no signal anywhere**. |
| F32 | CONFIRMED | `validate.py:20–33, 326–328`; `runner/backends.py:90–98, 202–208`; `fixes.py:351` | 24 hard-coded vocabulary literals across four files. |
| F33 | **DRIFTED** | `tests/test_sdk_backend.py`, `runner/backends.py:125–146` | Real SDK used when installed (not stubs). Missing: signature snapshot, live run, schedule. |
| F34 | CONFIRMED | `toggles.py:1–3`; `paths.py:89–93` | No marker written; no scan-scope check. |
| F35 | CONFIRMED | `sync.py:34–83`; `paths.py:182–191` | No probe of `settings.json` structure; a list-valued `mcpServers` would raise unhandled. |

---

## 4. Impact map

Files that must change per finding. "Docs" are S9 consumers. "Doctor" is the S10 guard.

| F# | Code | Tests | Docs / fixtures / goldens | Doctor rule (S10) |
|---|---|---|---|---|
| T0 | – | `test_easy_install`, `test_install`, `conftest` | – | – |
| F5 | `inventory.py` | `test_validate`/`test_manage` (new) | – | existing `load` (ERROR) now carries these |
| F14 | `fsutil.py` | `test_fsutil.py` (new) | CHANGELOG | – |
| F3 | `graph/store.py`, `hooks.py`, `dashboard/app.py` (overview) | `test_store_index.py` (new), `test_hooks` | README (perf claim), `scripts/bench.py` | **new** `runs.index.stale` (WARNING) |
| F2 | `runner/runner.py` | `test_runner` | `skills/graph-run/SKILL.md` (ownership rule) | – (event `report_conflict` is visible in ledger/CLI) |
| F6 | `install.py` | `test_install` | README (install crash-safety note) | **new** `install.interrupted` (WARNING) |
| F1 | `dashboard/app.py`, `static/app.js` | `test_dashboard` (+ R4 amendment A1) | README (dashboard auth), CHANGELOG | – |
| F4 | `easy-installer.bat`, `scripts/uv-pin.env` (new) | `test_easy_install` | README (install section), CHANGELOG | – |
| F7 | `toggles.py`, `inventory.py` | `test_manage` | – | **new** `disabled.stale` (INFO) |
| F8 | `fsutil.py` (helper), `migrate.py`, `fixes.py` | `test_manage`, `test_fixes` | – | – |
| F15 | `fsutil.py` | `test_fsutil.py` | – | – |
| F9 | `validate.py` | `test_validate` | – | existing codes (`unreadable`) |
| F10 | – (none) | `test_fixes` (characterization) | FIXLOG: N/A | existing |
| F11 | `redact.py` (new), `graph/store.py`, `validate.py` (re-export) | `test_redact.py`, `test_runner` | README (secrets note) | – |
| F16 | `mcp_registry.py` | `test_manage` | – | – |
| F17 | `mcp_server.py`, `graph/store.py` | `test_mcp_server` | – | – |
| F18 | `mcp_server.py` | `test_mcp_server` | – | – |
| F19 | `fixes.py`, `cli.py` | `test_fixes` | – | – |
| F12 | `scripts/easy_install.py` | `test_easy_install` | – | – |
| F13 | `scripts/easy_install.py`, `easy-installer.bat` | `test_easy_install` | README, CHANGELOG | – |
| F20 | `static/app.js`, `static/seq.js` (new) | `test_dashboard_js.py` (node) | – | – |
| F32 | `platform_vocab.py` + `data/platform_vocab.json` (new), `validate.py`, `runner/backends.py`, `fixes.py`, `migrate.py` | `test_platform_vocab.py` | README (compat notes), GEMINI/agent tools sync | **new** `vocab.override.invalid` (WARNING), updated INFO `agent.tools.unknown` |
| F31 | `hooks.py`, `hook_health.py` (new), `dashboard/app.py`, `static/app.js`, `validate.py` | `test_hooks_contract.py`, fixtures `tests/fixtures/hook_payloads/` | README (hooks), plugin docs | **new** `hooks.guard_blind` (WARNING) |
| F22 | `runner/backends.py` | `test_runner` | – | – |
| F23 | `runner/backends.py` | `test_sdk_backend` | – | – |
| F24 | `graph/schema.py`, `graph/templating.py`, `validate.py` | `test_engine` | `skills/graph-design/references/schema.md` | **new** `graph.input.name` (WARNING, F24b) |
| F25 | `static/app.js` | `test_dashboard.py` (static lint) | – | – |
| F26 | `runner/backends.py` | `test_sdk_backend`/`test_sdk_contract` | comment | – |
| F27 | `validate.py`, `inventory.py`, `graph/engine.py` | `test_validate`, `test_engine` | `scripts/bench.py` | – |
| F29 | `static/app.js`, `app.css`, `fixes.py`, `install.py` | `test_install`, `test_fixes` | – | – |
| F30 | `skills/graph-run/SKILL.md`, `GEMINI.md`, `agents/orchestrator.md`, `rules/orchestration.md`, `prompts/*`, `README.md` | `test_docs_sync.py` (new) | – | pytest guard (see S10 note) |
| F28 | `pyproject.toml`, `.github/workflows/ci.yml` | `test_easy_install`, `test_runner` | README (dev), CHANGELOG | – |
| F21 | – | `test_sdk_backend` (stub) | – | – |
| F35 | `sync.py`, `cli.py` | `test_manage` | – | – |
| F34 | `toggles.py`, `inventory.py`, `validate.py` | `test_manage` | – | **new** `disabled.in_scan_scope` (WARNING), `disabled.marker_leak` (WARNING) |
| F33 | – | `test_sdk_contract.py`, `tests/snapshots/` | `.github/workflows/sdk-contract.yml` | – |
| Phase 4 | – | `test_sync_audits.py` (new) | – | – |

---

## 5. Execution order (DAG)

Prescribed phase order is kept. Edges below are the **hard dependencies** (A → B: B must land after A). Starred edges were found in discovery and are not in the prompt.

```
F5  ─────────────────────────────────▶ F7 ──▶ F34*      (F34 adds the marker; F7 journals disable/enable)
F14 ──▶ F6 ; F14 ──▶ F7 ; F14 ──▶ F3* (index writes) ; F14 ──▶ F11* (report artifacts)
F3  ──▶ F17                                              (list(limit) uses the F3 index)
F9  ──▶ F32 (vocab override read uses the guarded reader) ; F9 ──▶ F27 (perf builds on the reader)
F32 ──▶ F31 (payload-key vocabulary) ; F32 ──▶ F34* (doctor scan-scope reads known roots)
F11 ──▶ F31*  (redact.py reused so alarm records never contain argument values)
F2  ──▶ F30  (ownership rule is written into graph-run SKILL, which F30 restructures)
F6  ──▶ F29* (install public API rename lands after install.py stops moving)
F20, F1 ──▶ F29* (JS refactor lands after the router/stream changes)
F21 ──▶ F26*, F33* (stub harness + real-SDK contract test share fixtures)
F28 last of the test work (coverage floor measured after all new tests exist)
F13/F4 share easy-installer.bat → F13 lands directly after F4's file is stable
```

Execution list (one commit per finding unless noted):

| Seq | Task | Phase | Est. diff |
|---|---|---|---|
| 0 | `docs(plan)`: this file | 0 | – |
| 1 | **T0** baseline test portability | 1 | ~30 |
| 2 | F5 | 1 | ~90 |
| 3 | F14 | 1 | ~120 |
| 4 | F3 (+ `scripts/bench.py` as a separate `chore` commit) | 1 | ~320 + 110 |
| 5 | F2 | 1 | ~110 |
| 6 | F6 | 1 | ~300 |
| 7 | F1 | 1 | ~260 |
| 8 | F4 | 1 | ~200 |
| 9 | F7 | 2 | ~140 |
| 10 | F8 | 2 | ~90 |
| 11 | F15 | 2 | ~90 |
| 12 | F9 | 2 | ~140 |
| 13 | F10 (test-only closure) | 2 | ~60 |
| 14 | F11 | 2 | ~130 |
| 15 | F16 | 2 | ~90 |
| 16 | F17 | 2 | ~100 |
| 17 | F18 | 2 | ~60 |
| 18 | F19 | 2 | ~90 |
| 19 | F12 | 2 | ~130 |
| 20 | F13 | 2 | ~80 |
| 21 | F20 | 2 | ~150 |
| 22 | F32 | 2 | ~330 |
| 23 | F31 | 2 | ~380 |
| 24 | F22 | 3 | ~100 |
| 25 | F23 | 3 | ~70 |
| 26 | F24a (F24b held, D1) | 3 | ~110 |
| 27 | F25 | 3 | ~50 |
| 28 | F26 | 3 | ~60 |
| 29 | F27 | 3 | ~260 |
| 30 | F29 | 3 | ~260 |
| 31 | F30 | 3 | ~160 |
| 32 | F28 | 3 | ~90 |
| 33 | F21 | 3 | ~180 |
| 34 | F35 | 3 | ~170 |
| 35 | F34 | 3 | ~260 |
| 36 | F33 | 3 | ~240 |
| 37 | Phase 4: `tests/test_sync_audits.py` + discrepancy fixes | 4 | ~300 + fixes |
| 38 | Phase 5: FIXLOG, CHANGELOG, FINAL-REPORT | 5 | docs |

FIXLOG.md is updated **in the same commit as each fix** (it is a ride-along artifact, SR3), not at the end.

---

## 6. Task cards

Conventions used on every card: **Status** (from §3) → **Problem / Root cause** → **Fix** → **Files** → **Tests** (each must fail on old code, R3) → **Acceptance** → **Risk & rollback** → **Sync** (Sync Map domains touched). Rollback is always `git revert <commit>` unless stated, since each task is one atomic commit.

### T0 — Baseline test portability (pre-existing failures)

* **Problem.** 3 tests fail on Windows at baseline (§2), which makes Gate G2 ("`pytest -q` fully green") unreachable on this platform and hides real regressions.
* **Root cause.** The tests encode POSIX assumptions, not product behaviour.
* **Fix.**
  1. `test_sh_half_parses`: feed the script on **stdin** (`subprocess.run([shell, "-n"], input=BAT.read_bytes())`). Same syntax check, no path translation, correct under the WSL launcher.
  2. `test_link_install`: skip with a reason when a symlink cannot be created (`conftest.symlinks_supported()` probe). Linux and CI Windows runners (which run elevated) still execute it.
  3. `test_agflow_command_prefers_the_running_executable`: derive the launcher name from `os.name` exactly as `agflow_command` does.
* **Files.** `tests/test_easy_install.py`, `tests/test_install.py`, `tests/conftest.py`.
* **Acceptance.** Windows: 0 failed (≤ 1 skipped for symlink privilege); Linux: identical results to baseline. Assertions are not weakened: each still asserts the same property.
* **R4 note.** Modifies three existing tests. Justification = the failing output in §2 plus the fact that production code is correct on Windows. Logged as amendments A2–A4 in §9 and in FIXLOG.
* **Risk & rollback.** Test-only. Est. ~30 lines.

### Phase 1 — P0

#### F5 🔴 One corrupt disabled-index entry crashes discovery

* **Status.** CONFIRMED, plus sibling F5b (corrupt `plugin.json`, unguarded `gemini_md.read_text`).
* **Root cause.** `Inventory.discover` mixes "scan what is there" with unguarded parsing of *agflow's own* state, and the mcp/hooks readers were guarded individually instead of centrally.
* **Fix.** In `inventory.py`: (a) read the index with `try/except (OSError, ValueError)` → `load_errors`; (b) reject a non-list top level; (c) wrap each entry in `try/except (KeyError, ValueError, TypeError)` and record `disabled index entry #i: <reason>` with the index file as path, then **continue**; (d) widen the existing mcp/hooks `except` to `(OSError, ValueError, AttributeError)` so unreadable/non-UTF-8 files behave like invalid ones; (e) F5b: guard the plugin manifest read and `gemini_md.read_text`. All problems surface through the existing `load_errors` → doctor `load` ERROR, i.e. the "visible warning" the registry asks for, aligned with `inventory.py:171–192`.
* **Files.** `src/agflow/inventory.py`.
* **Tests** (`tests/test_manage.py`): `test_discover_survives_corrupt_disabled_index` (parametrized: bad JSON, non-list, non-dict entry, bad kind, missing key), `test_discover_survives_corrupt_plugin_manifest`, `test_corrupt_index_reported_by_doctor` (asserts a `load` issue naming the file and that the *other* entries are still discovered).
* **Acceptance.** `Inventory.discover` never raises on these inputs; valid sibling entries still load; each problem appears as a doctor issue with the file path.
* **Risk & rollback.** Low. Doctor gains ERRORs where it previously crashed; nothing that worked changes.
* **Sync.** S2 (n/a), S10 (existing `load`). Est. ~90 lines.

#### F14 🟠→P0 Atomic write: no fsync, symlink and mode hazards

* **Status.** CONFIRMED (fsync absent; symlink/mode follow from semantics, proven on WSL in the gate).
* **Root cause.** `atomic_write_text` is an atomic-rename recipe missing the durability and identity-preservation steps.
* **Fix.** In `fsutil.py`:
  1. Resolve the destination: if `path` is a symlink, write through to `os.path.realpath(path)` (the link survives; a dangling link creates its target).
  2. Preserve the existing file's mode (`os.chmod(tmp, stat.S_IMODE(old.st_mode))`) on POSIX; new files keep today's `0600` (deliberately conservative: configs may hold env values). Windows: skipped (mode bits are advisory).
  3. `flush()` + `os.fsync(fd)` before `os.replace`, then fsync the parent directory on POSIX (opening a directory fails on Windows, so guarded by `os.name`).
  4. Keep the signature; add keyword-only `durable: bool = True` so a caller can opt out without a second code path.
* **Files.** `src/agflow/fsutil.py`.
* **Tests** (`tests/test_fsutil.py`, new): `test_fsync_called_for_file_and_directory` (monkeypatch `os.fsync`; directory fsync asserted on POSIX only), `test_symlinked_dotfile_stays_symlink` (skip if symlinks unsupported), `test_existing_mode_preserved` (POSIX), `test_new_file_mode_unchanged`, `test_failure_leaves_original_and_no_tmp`, `test_dangling_symlink_target_created`.
* **Acceptance.** All of the above; `write_json`/`write_yaml` inherit the behaviour.
* **Risk & rollback.** Latency: one fsync per write; measured in the bench (state writes on the runner path). Rollback: revert.
* **Sync.** S7 (manifest writes), S1 n/a. Est. ~120 lines.

#### F3 🔴 PostToolUse parses all runs on every tool call

* **Status.** CONFIRMED; **the registry's "key on runs-directory mtime" is insufficient** (mtime of `runs/` does not change when `runs/<id>/state.json` is replaced), and in-memory caching is useless because every hook is a new process.
* **Root cause.** "Which runs are active?" is answered by a full scan + pydantic validation of every `state.json`.
* **Fix.** Maintain a **light on-disk index** `<runs>/.index.json` (`{"version":1,"runs":{run_id:{graph_id,status,created_at,updated_at}}}`) in `RunStore`:
  * Updated inside `_write()` under its own `FileLock` after every durable state write (add/refresh the entry; uses F14's atomic write).
  * `active()` = `os.scandir(runs)` (names only, no per-file reads) → diff against the index (**self-healing**: unknown run dirs are parsed once and added; vanished dirs dropped) → full-validate **only** the entries marked `running` (truth wins; stale "running" entries are corrected). Cost is flat in the total number of runs.
  * `list(limit=None)` keeps today's semantics (full validate, skip corrupt, newest first). New `limit=N` parses only the top N by `created_at` from the index, continuing past corrupt ones until N valid are found (consumed by F17).
  * Corrupt or missing index → rebuilt from a scan, with a one-line `hook-debug`/ledger-visible `runs_index_rebuilt` event (R10).
  * On-disk change is additive (a new dot-file); older agflow ignores it, and `RunStore.run_dir()` already rejects ids starting with `.`.
* **Files.** `graph/store.py`, `hooks.py` (use `active()` unchanged; add nothing), `dashboard/app.py` (`overview` uses `active()` – no change needed), `validate.py` (doctor rule), `README.md`.
* **Doctor (S10).** New `runs.index.stale` WARNING when a full scan disagrees with the index (checked only when the workspace has runs; the check itself repairs nothing).
* **Tests** (`tests/test_store_index.py`, new): `test_active_matches_full_scan` (property-style over mixed statuses), `test_active_skips_corrupt_state_like_before`, `test_active_does_not_parse_terminal_runs` (monkeypatch `RunState.model_validate` counter: 500 terminal runs + 1 running → ≤ 2 validations), `test_index_self_heals_when_deleted_or_stale`, `test_list_limit_parses_only_limit`, `test_crash_between_state_and_index_is_corrected`, and a latency-shape test (`post_tool_use` at 1 vs 300 runs within a fixed multiple).
* **Acceptance.** Hook latency in-process: flat within noise from 1 → 500 runs (bench, §8 G3.4); behaviour identical for corrupt states. README claim replaced by the measured number (S9).
* **Risk & rollback.** Moderate: touches the core write path. Mitigations: the index can never be the source of truth (every consumer re-validates the entries it acts on); failure to update the index raises `OSError` *after* the durable state write, so state is never lost; rollback = revert (the dot-file is inert).
* **Sync.** S2 (state/ledger writers), S9 (README), S10 (`runs.index.stale`). Est. ~320 lines + `scripts/bench.py` ~110 in a separate `chore` commit.

#### F2 🔴 A concurrent MCP report destroys the headless run

* **Status.** CONFIRMED (also at the auto-approve call, `runner.py:43`).
* **Root cause.** `_report` is the only place a node's result is written, and it assumes it is the sole reporter; `engine.report` correctly rejects a stale report, but the runner treats that rejection as fatal and its `finally` then cancels everything.
* **Fix.**
  1. `_report` catches `engine.EngineError` and returns a conflict record; the runner logs `{"event": "report_conflict", "node": …, "reason": …}` (ledger + `on_event`, so the CLI prints it) and **continues**. State is untouched on `EngineError` because `transaction()` skips `_write` when its body raises.
  2. Same guard on the `--approve-human` auto-approval path.
  3. **Ownership rule** (documented in `skills/graph-run/SKILL.md`, README run section): *whoever claimed a node owns its report; a report for a node that is no longer `running` is rejected and the late reporter discards its result. Do not drive one run from the headless runner and the MCP loop at the same time.* The runner's `finally` already releases only nodes it still tracks.
* **Files.** `runner/runner.py`, `skills/graph-run/SKILL.md`, `README.md`.
* **Tests** (`tests/test_runner.py`): `test_concurrent_mcp_report_does_not_kill_run` (reproduces the probe: backend reports the node via the store first), `test_conflict_is_logged_not_swallowed`, `test_human_auto_approve_conflict_is_tolerated`, `test_unrelated_exception_still_aborts_run` (guards against over-catching).
* **Acceptance.** The probe scenario completes with `status == succeeded`, the MCP result wins for the conflicting node, a `report_conflict` event exists, no node is left `running`.
* **Risk & rollback.** Low. Only `EngineError` is caught.
* **Sync.** S2, S5 (SKILL text), S9. Est. ~110 lines.

#### F6 🔴 Non-atomic plugin switch during install

* **Status.** CONFIRMED.
* **Root cause.** The destructive step (remove old plugin) precedes the only durable record of its backup (manifest, written last), and the new bundle is built *in place* after the removal.
* **Fix** (journal pattern: **back up → record → act**):
  1. Build the new bundle in a sibling staging dir `…/.agflow-new-<hex>` (copy, profile filter, command wiring). Any failure here leaves the live plugin untouched.
  2. `record_write(path)` persists the manifest **immediately after taking each backup and before the destructive action** (atomic write, F14).
  3. Swap with two renames: live → `…/.agflow-old-<hex>`, staging → live; delete the old dir last. A rename failure (Windows file lock) aborts with `InstallError` and restores the old dir.
  4. `--link` mode: create the link at a temp name and `os.replace` it; if a real directory exists, same rename dance.
  5. `install()`/`uninstall()` begin with `recover_interrupted()`: if a `.agflow-old-*` exists and the live target is missing, rename it back; leftover staging dirs are removed; each action is reported in the plan.
* **Files.** `install.py` (+ public API additions used by F29), `validate.py` (doctor rule).
* **Doctor (S10).** New `install.interrupted` WARNING: leftover `.agflow-new-*`/`.agflow-old-*` siblings, or a manifest `created` entry whose target is missing.
* **Tests** (`tests/test_install.py`): crash-injection matrix via monkeypatch at each step (`copy_tree`, manifest write, first rename, second rename, GEMINI.md write): assert after each simulated crash that (i) the previous plugin is intact **or** recoverable by `recover_interrupted()`, (ii) `uninstall` restores the pre-install tree exactly, (iii) doctor reports `install.interrupted` while debris exists. Plus `test_install_uninstall_roundtrip_tree_identical` (S7), `test_link_install_replaces_atomically` (skip w/o symlinks), `test_rename_failure_keeps_old_plugin`.
* **Acceptance.** No injected crash leaves the user without a working plugin or without a manifest that references every backup taken.
* **Risk & rollback.** Highest-risk P0 change (core installer). Mitigation: the crash matrix; no change to manifest *schema* (R2), only to *when* it is written. Rollback: revert.
* **Sync.** S7 (manifest ↔ uninstall ↔ fixes `_fix_reinstall_agflow`), S1, S9, S10. Est. ~300 lines.

#### F1 🔴 SSE auth token leaks in the query string

* **Status.** CONFIRMED.
* **Root cause.** `EventSource` cannot set headers, so the long-lived bearer token was put in a URL.
* **Fix** (one-time ticket):
  1. `POST /api/stream-ticket` (header token required) with `{run_id}` → `{ticket, expires_in}`; a 128-bit `secrets.token_urlsafe` ticket, ~30 s TTL, **single use**, bound to the run id, held in an in-memory dict with lazy expiry sweep and a hard size cap.
  2. `GET /api/runs/{id}/stream?ticket=…` is the **only** place a query credential is accepted; it is consumed on connect. `?token=` is rejected on every route.
  3. `app.js`: obtain a ticket (header-authenticated POST), then open `EventSource`; on `error` the client closes, fetches a **new** ticket and reconnects with capped backoff (a consumed ticket cannot be replayed, and `EventSource` does not retry on a 401).
  4. The `#token=` fragment bootstrap is unchanged: the fragment token still goes to `sessionStorage` and is used only as a header.
  5. `create_app(workspace, token, *, ticket_ttl=30.0, clock=time.monotonic)` — additive keyword-only parameters make expiry testable without sleeping.
* **Files.** `dashboard/app.py`, `dashboard/static/app.js`, `README.md`.
* **Tests** (`tests/test_dashboard.py`): `test_query_token_rejected_everywhere` (every route), `test_ticket_requires_header_token` (401), `test_ticket_single_use`, `test_ticket_expires` (fake clock), `test_ticket_bound_to_run`, `test_stream_connects_with_ticket` (reads the first SSE frame), `test_ticket_store_bounded`.
* **R4 amendment A1.** `test_auth_and_host_checks` line 25 asserts `params={"token": TOKEN}` → 200. That assertion *is* the vulnerability F1 orders removed. Changed to assert **401**; no other assertion in the test changes.
* **Acceptance.** `grep -rn "token=" src/agflow/dashboard/static` finds nothing; no route accepts a long-lived token from the URL; the stream works end to end in the Browser pane (visual check, §8).
* **Risk & rollback.** Moderate: SSE reconnect logic. CHANGELOG "Security" + "Changed" note (external `curl …?token=` use no longer works; use the header).
* **Sync.** S6 (endpoint ↔ fetch URLs), S9. Est. ~260 lines.

#### F4 🔴 Unpinned `curl | sh` / `irm | iex` bootstrap

* **Status.** CONFIRMED (`easy-installer.bat:42, 87, 89`).
* **Root cause.** Trust-on-first-use of whatever the CDN serves, executed through a pipe, so nothing can be verified before execution.
* **Fix.**
  1. New `scripts/uv-pin.env` — the single source of truth: `AGFLOW_UV_VERSION`, `AGFLOW_UV_SH_SHA256`, `AGFLOW_UV_PS1_SHA256`. Both halves of the polyglot read it (`. ./scripts/uv-pin.env` / a `for /f` loop in the batch half — no labels/goto, preserving the existing polyglot test).
  2. **Download to a file, verify, then run** — never pipe. Versioned URL: `https://github.com/astral-sh/uv/releases/download/<ver>/uv-installer.sh|ps1`. Verification: `sha256sum` → `shasum -a 256` → `openssl dgst -sha256` on POSIX; `Get-FileHash` on Windows. Mismatch ⇒ delete the file, print expected vs actual, exit 1 (no fallback to unverified).
  3. `AGFLOW_UV_VERSION` override for CI: allowed only together with `AGFLOW_UV_INSTALLER_SHA256`; otherwise a clear refusal. `AGFLOW_UV_INSTALLER_URL` is honoured for tests (documented as test-only).
  4. The cargo-dist installers themselves verify the binary archive checksum, so the chain is pinned-hash → installer → embedded archive hashes → binary.
  5. Non-interactive consent for this step follows F13 (see there).
* **Hash provenance.** Digests come from GitHub release **metadata** (`gh api repos/astral-sh/uv/releases/tags/<ver>` → `assets[].digest`), not from a download; D4 asks before any download-based cross-check.
* **Files.** `easy-installer.bat`, `scripts/uv-pin.env` (new), `README.md`, `.github/workflows/ci.yml` (installer job unchanged: it exercises the real path).
* **Tests** (`tests/test_easy_install.py`): `test_no_pipe_to_shell_in_installer` (regex: no `| sh`, `| iex`, `|sh`), `test_pin_file_wellformed` (version semver, two 64-hex digests), `test_sh_half_rejects_hash_mismatch` (runs the sh half under `sh` with a stub `curl`/`wget` returning a file with the wrong hash and `AGFLOW_UV_INSTALLER_URL` pointing to it; expects exit ≠ 0 and the mismatch message; skipped if no `sh`), `test_sh_half_accepts_matching_hash`, `test_version_override_requires_checksum`. Existing polyglot-structure tests must still pass unchanged.
* **Acceptance.** G3.5: no unpinned `curl|sh`. The `ps1` path is verified manually on this Windows machine against a locally served file (not the network) and by the CI `installer` job on real runners.
* **Risk & rollback.** Installer is user-facing and cross-platform. Rollback: revert; the pin file is inert.
* **Sync.** S9 (README install section), polyglot invariants. Est. ~200 lines.

### Phase 2 — P1

#### F7 🟠 Non-atomic disable orphans the parked item

* **Status.** CONFIRMED (probe: crash → item in parking, invisible).
* **Root cause.** The move is the first durable effect; the index is written after it.
* **Fix.** Journal-first: (1) write the index entry (atomic, F14); (2) move; (3) if the move raises, remove the entry and re-raise. Crash between 1 and 2 leaves a harmless entry whose `parked_path` is missing while `original_path` exists. `enable()` self-heals that case (drops the stale entry, reports "already enabled"); `disable()` removes stale duplicates of the same item before appending; `_scan_disabled` already skips missing parked paths and now notes them.
* **Files.** `toggles.py`, `inventory.py`.
* **Doctor.** New `disabled.stale` INFO for entries whose parked path is missing.
* **Tests** (`tests/test_manage.py`): `test_crash_between_index_and_move_keeps_item_enabled`, `test_crash_after_move_item_discoverable_and_enableable`, `test_failed_move_rolls_back_index`, `test_stale_entry_pruned_on_enable_and_disable`.
* **Acceptance.** For every injected crash point the item is either still enabled and visible, or disabled and re-enableable; never lost.
* **Risk & rollback.** Low. Index format unchanged (same keys).
* **Sync.** S1, S7-like (index ↔ inventory), S10. Est. ~140 lines.

#### F8 🟠 `.bak` rename: Windows crash / POSIX silent overwrite

* **Status.** CONFIRMED on Windows (`FileExistsError`); POSIX overwrite by `rename(2)` semantics.
* **Root cause.** A fixed backup name with a rename that has platform-dependent overwrite semantics.
* **Fix.** `fsutil.unique_sibling(path, suffix=".bak")` returns the first unused of `x.bak`, `x.bak2`, `x.bak3`, …; `migrate_workflows` and `fixes._fix_workflow` both use it, and the preview text (`report.actions`, fix `changes`) shows the **actual** chosen name in dry-run too. No overwrite on any OS.
* **Files.** `fsutil.py`, `migrate.py`, `fixes.py`.
* **Tests.** `test_manage`: `test_migrate_workflow_keeps_existing_bak` (`.bak` and `.bak2` both present after two runs; fails on Windows today with `FileExistsError`, and on POSIX by overwriting), `test_dry_run_reports_actual_bak_name`; `test_fixes`: `test_workflow_fix_uses_unique_bak_and_undoes`; `test_fsutil`: `test_unique_sibling_sequence`.
* **Acceptance.** Re-running migration never loses a backup; messages match actions.
* **Risk & rollback.** Low. Sync: S1, S7 (fix ↔ migrate parity), S9. Est. ~90 lines.

#### F15 🟠 Same-name backup collision silently loses a backup

* **Status.** CONFIRMED (file and directory cases).
* **Root cause.** The fallback destination is the bare file name, and `dirs_exist_ok=True` merges instead of failing.
* **Fix.** Outside-anchor paths map to `<backup_root>/_external/<sha1(abs path)[:12]>/<name>`; `dirs_exist_ok=False`; if the destination already exists for the *same* source path in the same root, the first (pristine) backup wins and is returned unchanged. Anchor-relative paths keep their layout. Existing manifests store absolute backup paths, so old installs still uninstall (backward compatible).
* **Files.** `fsutil.py`.
* **Tests** (`test_fsutil.py`): `test_same_name_files_from_different_dirs_both_restorable`, `test_same_name_dirs_not_merged`, `test_same_path_twice_keeps_first`, `test_anchor_relative_layout_unchanged`, `test_old_manifest_restores` (hand-written v0.2.0-style manifest).
* **Risk & rollback.** Low. Sync: S7. Est. ~90 lines.

#### F9 🟠 Unguarded reads can crash doctor

* **Status.** CONFIRMED (`validate.py:69, 216, 245, 271`; plus `:94, :314`).
* **Root cause.** Reading was not centralised: `_load` guards, six other call sites don't.
* **Fix.** One guarded reader, `_read_text(path) -> (text | None, [Issue])`, used by every validator (`validate_gemini_md`, `validate_rule` second read, `validate_mcp_file`, `validate_hooks_file`, `validate_plugin_manifest`, always-on counting in `validate_inventory`); `_load` is rebuilt on it. Unreadable/vanished/non-UTF-8 ⇒ `unreadable` ERROR and processing continues. Empty files are treated uniformly (`or "{}"` semantics) so `plugin.json`, mcp and hooks behave the same.
* **Files.** `validate.py`.
* **Tests** (`test_validate.py`): parametrized `test_validators_never_crash_on_vanished_unreadable_binary_or_empty`, `test_inventory_validation_survives_file_deleted_after_discovery`.
* **Risk & rollback.** Low. Sync: S10 (existing `unreadable`). Est. ~140 lines.

#### F10 🟠 Legacy migration undo — **N/A (not reproduced)**

* **Evidence.** §3. Both branches of `_fix_legacy` back up exactly the directories `migrate_legacy_*` mutate; undo restored an identical tree.
* **Action.** No production change. Add `test_fix_legacy_undo_restores_tree_workspace_and_global` to lock the invariant that the finding was worried about. FIXLOG records `N/A` with the probe output.
* **Out of scope, logged in §11:** `agflow migrate`/`/api/migrate` perform moves/renames *without* a transaction by design.
* Est. ~60 lines (test only).

#### F11 🟠 Graph inputs persisted to the ledger unredacted

* **Status.** CONFIRMED (+ same sink class: `report.md` inputs section, `report.json` via `status_view`).
* **Root cause.** The run-artifact writers serialise `inputs` verbatim; there is no shared notion of "secret-looking".
* **Fix.** New stdlib-only `agflow/redact.py`: `REDACTED = "***REDACTED***"`, `SECRET_KEY_RE` (moved from `validate.py`, which re-exports it — one source of truth), `redact_mapping()` (deep, by key name) and a conservative value scan for well-known token shapes (`sk-…`, `ghp_…`, `AIza…`, `Bearer …`, PEM private-key headers). Applied to: `run_started` ledger inputs, `render_report_md`, `report.json`. `state.json` keeps real values **by necessity** (prompts render `{{ inputs.* }}`); this is documented in the README run section.
* **Files.** `redact.py` (new), `graph/store.py`, `validate.py`, `README.md`.
* **Tests.** `test_redact.py` (key and value patterns, nesting, non-secret passthrough, idempotence); `test_runner.py`: `test_secret_input_redacted_in_ledger_and_reports_but_usable_in_prompt`.
* **Acceptance.** `grep` of a run dir for the secret finds it only in `state.json`.
* **Risk & rollback.** Low; false positives only blank a display value. Sync: S2, S9. Est. ~130 lines.

#### F16 🟠 `mcpServers` lacks type validation

* **Status.** CONFIRMED (TypeError ×2, JSONDecodeError).
* **Fix.** `_load` validates: top-level object, `mcpServers` is an object, every entry is an object, invalid JSON/encoding ⇒ `McpError` with file path and entry name; callers (CLI/dashboard) already map `McpError` to a message/HTTP 400.
* **Files.** `mcp_registry.py`. **Tests** (`test_manage.py`): parametrized malformed-config cases for `list/add/remove/set_enabled`, each raising `McpError` (not `TypeError`/`ValueError`); dashboard returns 400 (`test_dashboard`).
* **Risk & rollback.** Low. Est. ~90 lines.

#### F17 🟠 `graph_status`/`graph_runs` fully validate all runs per poll

* **Status.** CONFIRMED. **Depends on F3.**
* **Fix.** `graph_status` without `run_id` uses `store.list(limit=1)`; `graph_runs` uses `store.active()` or `store.list(limit=50)`. Responses are byte-identical in shape.
* **Files.** `mcp_server.py`. **Tests** (`test_mcp_server.py`): `test_graph_runs_parses_at_most_limit` (validation counter with 200 runs), `test_graph_status_latest_run_unchanged`.
* **Risk & rollback.** Low. Est. ~100 lines.

#### F18 🟠 `_workspaces` has no eviction

* **Status.** CONFIRMED.
* **Fix.** Bounded LRU (`collections.OrderedDict`, cap = `store.RUN_INDEX_MAX`), `_remember()` helper used at both insert sites; lookups refresh recency. Kept in memory (rather than only the disk index) so resolution still works if the disk index is unwritable.
* **Files.** `mcp_server.py`. **Tests:** `test_workspace_cache_is_capped_and_lru`. **Risk:** low. Est. ~60 lines.

#### F19 🟠 `apply()` performs two full discoveries

* **Status.** CONFIRMED (2 inside `apply`, 3 for CLI flows that show a plan first).
* **Fix.** `apply(workspace, selections, *, plan: FixPlan | None = None)`: when a plan is supplied it is used as-is (the CLI interactive flow builds it milliseconds earlier); otherwise one is built. The post-apply re-check stays one fresh discover+validate (the tree changed; this one is necessary). The dashboard keeps re-planning inside `apply` (its GET and POST are far apart, so reuse would be stale).
* **Files.** `fixes.py`, `cli.py`. **Tests** (`test_fixes.py`): `test_apply_with_plan_discovers_once`, `test_apply_without_plan_discovers_twice_max`, behaviour parity of `ApplyResult`.
* **Risk & rollback.** Low. Est. ~90 lines.

#### F12 🟠 Force-kill matches processes by loose `CommandLine`

* **Status.** CONFIRMED.
* **Fix.** Drop the `CommandLine` clause: match only `ExecutablePath` under the tool env or equal to the launcher. Record `CreationDate` at detection; immediately before `taskkill`, re-query each PID and require the same `ExecutablePath` **and** `CreationDate` (PID-reuse guard); skip and report any that changed. Pure helpers (`owned_by_tool(proc, env, exe)`, `same_identity(a, b)`) keep the policy testable off-Windows.
* **Files.** `scripts/easy_install.py`. **Tests:** `test_editor_with_folder_open_not_matched`, `test_pid_reuse_not_killed`, `test_same_identity_kills`, `test_parse_processes_keeps_creation_date`. **Parity (R7):** manual run on this Windows machine against a throwaway process tree; recorded in FIXLOG.
* **Risk & rollback.** Medium (Windows-only behaviour). A child interpreter not under the env but spawned by an in-env launcher is still reached by `taskkill /T` on its parent. Est. ~130 lines.

#### F13 🟠 `confirm()` auto-approves without a tty

* **Status.** CONFIRMED (+ the sh half of `easy-installer.bat` shares the pattern).
* **Fix.** `confirm()`: `assume_yes` ⇒ True; no tty ⇒ raise `InstallFailed("non-interactive session: re-run with --yes to approve")`; tty ⇒ prompt. The sh half applies the same rule before installing uv (no tty and no `--yes` ⇒ exit 1 with the same message).
* **Files.** `scripts/easy_install.py`, `easy-installer.bat`, `README.md`.
* **Tests:** `test_confirm_non_tty_without_yes_aborts`, `test_confirm_yes_proceeds_without_tty`, `test_confirm_tty_prompts`, `test_sh_half_noninteractive_without_yes_refuses` (stubbed PATH without uv; skipped if no `sh`).
* **R2.** Behaviour tightening, registry-mandated (D2): CI already uses `--yes`; documented migration note.
* **Risk & rollback.** Low. Est. ~80 lines.

#### F20 🟠 Stale-render race in router and graph-editor validate

* **Status.** CONFIRMED (0 guards).
* **Fix.** Extract the logic into a tiny dependency-free `static/seq.js` (`latest()` → `{begin(), isCurrent(token)}`) loaded by `index.html`: `route()` takes a token before awaiting and drops a stale result; an `AbortController` per route cancels in-flight `api()` calls (`api()` gains an optional `signal`); the graph editor's debounced `validate()` uses its own token; a superseded run-detail page closes the `EventSource` it created. `AbortError` is ignored.
* **Files.** `static/app.js`, `static/seq.js` (new), `static/index.html`.
* **Tests** (`tests/test_dashboard_js.py`): runs `node --test` over `tests/js/seq.test.mjs` (skipped when `node` is absent; CI ubuntu has Node), plus Python static assertions that `app.js` calls `latest()` in `route` and `validate`. Manual race reproduction in the Browser pane (A→B navigation with a delayed A) recorded in FIXLOG.
* **Risk & rollback.** Low–moderate (UI). Est. ~150 lines.
* **Sync.** S6.

#### F32 🟠 Antigravity vocabulary hard-coded, no version negotiation

* **Status.** CONFIRMED (24 literals across 4 files).
* **Fix.**
  1. New `platform_vocab.py` + packaged data `data/platform_vocab.json` (single source): `vocab_version`, agent tools (the 25), **SDK tool map** (the 19 `TOOL_MAP` entries), model arrows, execution policies, rule triggers, hook events, hook-payload key variants, and `workflows_retired_on` (the date is **data**, not code).
  2. Optional user override `<global_state_dir>/vocab.json` (and `<ws>/.agents/agflow/vocab.json`) — merged over the built-in table, read with F9's guarded reader; invalid override ⇒ ignored **loudly** (doctor `vocab.override.invalid` WARNING + INFO naming the active vocab versions).
  3. Consumers derive from it: `validate.py` (checks), `backends.TOOL_MAP` (built-in alias for compat; `build_config` uses the effective map), `backends.model_for_tier` (env var `AGFLOW_MODEL_<TIER>` generated from the model arrows, so a new tier needs no code), `fixes.py:351`, the `workflow.deprecated` message date.
  4. Unknown tool names ⇒ INFO `agent.tools.unknown`: *"unknown to this agflow version (vocab vX): …"*.
  5. Hatch already ships non-Python files under the package; a test asserts the data file is in the wheel.
* **Files.** `platform_vocab.py` + `data/platform_vocab.json` (new), `validate.py`, `runner/backends.py`, `fixes.py`, `migrate.py`.
* **Tests** (`test_platform_vocab.py`): built-in table equals the previous constants (golden), override merge/remove, invalid override → warning not crash, unknown-name message contains the vocab version, date parse, `TOOL_MAP` derivation, wheel includes data file.
* **Acceptance (S4 audit).** grep for the hard-coded names outside the vocab module = 0.
* **Risk & rollback.** Moderate (touches four modules) but mechanical; the golden test pins identical behaviour. Est. ~330 lines.

#### F31 🟠→🔴 Hook protocol undocumented; guard fails open silently

* **Status.** CONFIRMED. **Depends on F32 (payload-key vocabulary) and F11 (redaction).**
* **Root cause.** Fail-open is the right default for a *hook that must not break the agent* — but it was implemented as *fail-silent*, so a protocol change disables the 25-rule guard with no signal anywhere.
* **Fix (fail loud, still fail open for the agent).** Output to Antigravity is unchanged (`{}`); what changes is that every degraded case now leaves evidence:
  1. **Shape detection** (pure, in `hooks.py`): `payload_not_json`, `unknown_shape` (non-empty dict with no known tool-name *and* no known args key), and `guard_blind` (a known command/file tool whose command/path could not be extracted — the case where Google renames the *argument* keys). Key sets come from `platform_vocab`.
  2. **Signals**, all unconditional (not gated on `AGFLOW_HOOK_DEBUG`): a line in `hook-debug.jsonl` with the *key names only* (never values — redaction safe); a counter file `~/.gemini/config/agflow/hook-health.json` (`{"unknown_shape":N,"guard_blind":N,"handler_error":N,"first_seen","last_seen","last_keys"}`); a `guard_unavailable` event in the ledger of every active run (dashboard shows it); and the swallowed-exception path counts `handler_error`.
  3. **Surfaces:** `/api/overview` gains `hook_health`; the dashboard shows a red banner when non-zero; `agflow doctor` emits WARNING `hooks.guard_blind` (with counts and first/last seen) and clears itself after `agflow hook-health --reset`/age-out (14 days).
  4. **Contract tests with real payloads.** Antigravity has not published a schema and none can be fetched from here. `tests/fixtures/hook_payloads/*.json` carries each sample with a `_provenance` field (`captured` | `synthetic` | `sdk-doc`); the contract test iterates the directory, so any payload captured with the existing `AGFLOW_HOOK_DEBUG=1` can be dropped in. **Samples that I cannot capture will be labelled `synthetic` and I will say so in FIXLOG and the final report; I will not call them "real".**
* **Files.** `hooks.py`, `hook_health.py` (new, stdlib-only), `dashboard/app.py`, `dashboard/static/app.js`, `validate.py`, `cli.py` (`hook-health` subcommand is *not* added; doctor is the surface), `README.md`, `tests/fixtures/hook_payloads/`.
* **Tests** (`test_hooks_contract.py`): every fixture classifies as expected; the F31 probe (unknown shape + `rm -rf /`) now produces a debug line, a counter increment, a ledger event, and a doctor WARNING; `test_output_unchanged_fail_open`; `test_alarm_contains_keys_not_values`; `test_handler_exception_counted`; `test_bad_stdin_still_returns_empty_json` (existing test keeps passing).
* **Acceptance.** No payload that reaches the handler can silently disable the guard.
* **Risk & rollback.** Moderate: hook hot path. The alarm path is only taken on degraded input; the normal path adds one set-membership test. Counter writes are atomic (F14). Est. ~380 lines.

### Phase 3 — P2

#### F22 🟡 Fragile JSON-extraction regex

* **Status.** CONFIRMED with corrected triggers (`} ``` ` inside a string; top-level arrays).
* **Fix.** Replace the regex with a small **scanner**: find each fence (```` ``` ````[lang]), take the block up to the *matching* closing fence by `json.JSONDecoder().raw_decode` from the first `{`/`[` (string-aware by construction), validate against `NodeResult`; accept a top-level array by taking the last element that validates; later blocks win (as today). Unfenced-object behaviour is unchanged (still "no valid result block": the result contract demands a fence).
* **Files.** `runner/backends.py`. **Tests** (`test_runner.py`): brace+fence inside a string, top-level array, nested objects, two blocks → last wins, prose braces before the block, trailing junk, invalid JSON, empty. All previous `parse_result_text` tests unchanged.
* **Risk & rollback.** Low. Est. ~100 lines.

#### F23 🟡 `timeout_min=None` → message-less TypeError

* **Fix.** Pure `timeout_seconds(card) -> float`: missing/`None`/non-numeric/bool ⇒ default 30; clamp to `[1, 1440]` minutes (the schema's bounds). Timeout result names the node: `node '<id>' timed out after N min`. Run-level assertion lands with F21's stub harness.
* **Files.** `runner/backends.py`. **Tests:** parametrized `timeout_seconds`; timeout message includes node id (F21 harness). Est. ~70 lines.

#### F24 🟡 Duplicate `depends_on` and free-form `inputs` keys

* **Split because of R2 (D1).**
  * **F24a (proceeds):** reject duplicate `depends_on`; fix `{{ inputs.items }}` at the root cause — the sandboxed Jinja environment resolves **item-first** for mappings (`env.getattr` override in `templating.py`), so an input named `items`/`keys`/`values` returns the *value*, not a bound method. Template semantics change only where a key collided with a dict attribute.
  * **F24b (HELD for decision D1):** enforce `ID_RE` on `inputs` keys. Default if no answer: **warn** (doctor `graph.input.name` WARNING) in 0.3.0, error in 0.4.0. Reason: rejecting `Feature`/`bugID` in existing user graphs is a break.
* **Files.** `graph/schema.py`, `graph/templating.py`, `validate.py` (warning), `skills/graph-design/references/schema.md`.
* **Tests** (`test_engine.py`): duplicate deps rejected with a message naming node and dep; `inputs.items` renders the value; bundled graphs still valid; all existing graph tests unchanged.
* **Risk & rollback.** Low (F24a). Est. ~110 lines.

#### F25 🟡 Dead `innerHTML` sink

* **Fix.** Delete the `html:` branch in `h()`. Add a static test that fails on any of `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval(`, `new Function` in `static/*.js` (comments stripped).
* **Files.** `static/app.js`. **Tests:** `test_dashboard.py::test_no_html_sinks_in_static_js` (fails on old code). Est. ~50 lines.

#### F26 🟡 Policy ordering undocumented

* **Fix.** Comment at the `allow_all()` site quoting the SDK's documented precedence; `test_sdk_contract.py::test_deny_outranks_allow_all_in_both_list_orders` uses the real SDK's `policy.enforce` with both orderings and asserts the guard's `run_command` deny wins; skipped (not failed) only if the SDK is absent, and made mandatory in the nightly (`AGFLOW_REQUIRE_SDK=1`, F33).
* **Files.** `runner/backends.py`. Est. ~60 lines.

#### F27 🟡 Remaining performance drains (severe: doctor 63 s)

* **Fix.**
  1. `_ref_exists`: per-validation `_FsCache` (one `scandir` per directory, lookups by name; on a *miss* fall back to the original `Path.exists()` so case-insensitive filesystems keep their current behaviour — R7). `validate_skill(path, *, cache=None)` stays call-compatible. Sibling-skill directory list computed once per skills root, not per reference.
  2. Rule files read once per phase: `Inventory` stores `meta["chars"]` for rules (as it already does for GEMINI.md); `validate_rule` takes its size from the text it already loaded; always-on counting uses `meta["chars"]`. Target ≤ 2 reads of any file across discover+validate (from 4).
  3. `engine.advance`: a pass-local evaluation context. `outputs` depend only on inputs/run, so they are rendered once per pass; `nodes.<id>.{status,summary,artifacts}` are updated incrementally when `_set` changes a node. `context()` (public) is untouched. Result equivalence is proven on the bundled graphs and a randomized-graph differential test.
* **Files.** `validate.py`, `inventory.py`, `graph/engine.py`, `scripts/bench.py`.
* **Tests:** `test_validate`: `test_ref_check_stat_count_is_linear` (counts `os.scandir` calls for 120 skills), `test_rule_file_read_at_most_twice`; `test_engine`: `test_advance_matches_reference_context` (differential vs the old per-`when` context), `test_advance_renders_outputs_once_per_pass`.
* **Acceptance (G3.4).** `bench.py` doctor on the 350-item tree: from **63,365 ms** to well within the 2× criterion (target: orders of magnitude lower; the number goes in the report).
* **Risk & rollback.** Moderate for the engine part (pure state machine, covered by differential test). Est. ~260 lines.

#### F29 🟡 Frontend duplication; fixes reaching into install privates

* **Fix.**
  1. `install.py`: publish `manifest_path`, `plugin_target`, `BLOCK_RE`, `master_rule`, `MASTER_RULE`; `fixes.py` uses only the public names. No test or module in the repo imports the old underscore names (verified), so they are renamed, not aliased.
  2. `app.js`: `componentPage(kind, options)` gains `{cols, describe, intro}`; the rules page is built from it; one `undoFix()` used by both undo call sites.
  3. `app.css`: the dark palette is defined **once** with `light-dark()` tokens and `color-scheme: light dark`, with `[data-theme]` overriding `color-scheme` only (verified visually in the Browser pane in both themes; shadow tokens split into colour + geometry).
* **Files.** `install.py`, `fixes.py`, `static/app.js`, `static/app.css`.
* **Tests:** `test_install.py`/`test_fixes.py` exercise the public API; `test_dashboard.py::test_no_duplicate_dark_palette` (the dark hex block appears once); import test that `fixes.py` references no `inst._` attribute (AST). Est. ~260 lines.

#### F30 🟡 Documentation drift between dispatch layers

* **Fix.** `skills/graph-run/SKILL.md` becomes the **single canonical MCP reference**: a "Tool reference" table with every tool's exact signature generated from `mcp_server`'s registered signatures. `GEMINI.md`, `agents/orchestrator.md`, `rules/orchestration.md`, `prompts/orchestrate.md`, `README.md` name tools and *link* to the skill instead of restating argument lists; any retained inline signature must equal the canonical one.
* **Machine enforcement** (`tests/test_docs_sync.py`): (1) the canonical table equals the live tool signatures (via the server's tool registry/`inspect`); (2) every `graph_*(…)` call-with-arguments in `plugin/` and `prompts/` equals the canonical signature; (3) every other mention is bare-name. S10 note: this invariant concerns the repository's own docs, not user config, so the guard is a pytest check rather than a doctor rule.
* **Files.** the five docs above, `tests/test_docs_sync.py`. Est. ~160 lines.

#### F28 🟡 No coverage gate; timing patterns in tests

* **Fix.** Add `pytest-cov` to the `dev` group; `[tool.coverage.run] source=["agflow"]`; CI runs `pytest -q --cov=agflow --cov-report=term-missing --cov-fail-under=84` (the floor is **not** in `addopts`, so running a single test file locally doesn't fail). Replace `time.sleep(0.01)` with `os.utime` on the fixtures and the `asyncio.sleep(0.2)` race with an `asyncio.Event` set from `FakeBackend.on_run`. CI `test` job gains `windows-latest` and `macos-latest` to enforce R7 (delivered, unverified on real runners — said so in the report). Final step: raise the floor to the measured value minus 1.
* **R4 note.** The two timing replacements alter existing tests without weakening them (amendments A5–A6).
* **Files.** `pyproject.toml`, `.github/workflows/ci.yml`, `tests/test_easy_install.py`, `tests/test_runner.py`, `uv.lock` (dev dep). Est. ~90 lines.

#### F21 🟡 `SdkBackend.run` untested

* **Fix.** Unconditional stub of `google.antigravity` (`monkeypatch.setitem(sys.modules, …)` providing `Agent` as an async context manager with `chat()` → response having `structured_output()`/`text()`), and `build_config` replaced by a sentinel so `run` is tested in isolation. Branches: structured success, structured invalid → text fallback, `None` structured → text fallback, no JSON → `failed`, timeout → `failed` naming the node (completes F23), config build failure.
* **Files.** `tests/test_sdk_backend.py` (stub tests placed *before* the `importorskip` so they always run). **Acceptance.** `backends.py` coverage 79% → ≥ 95%. Est. ~180 lines.

#### F35 🟡 `sync` adds an unprobed second-product dependency

* **Fix.** `sync.probe_gemini_cli(settings_file, skills_dest)` runs before any write (also in dry-run): settings must be a JSON object, `mcpServers` (if present) an object of objects, skills destination must be a directory or absent; an optional `gemini --version` read (3 s timeout, only if on PATH) is reported as context. Mismatch ⇒ `SyncError` with an actionable message naming the file and what was expected; nothing is written. `Probe` is a tiny protocol so a future target reuses it.
* **Files.** `sync.py`, `cli.py` (`fail()` on `SyncError`). **Tests** (`test_manage.py`): list-valued `mcpServers`, non-object settings, file-as-skills-dir, valid settings pass, dry-run also probes, no partial writes on failure. Est. ~170 lines.

#### F34 🟡 Toggles rely on negative-space discovery

* **Fix.** Second defence layer:
  1. `disable()` writes a **marker** `.agflow-disabled` (JSON: kind, name, scope, original_path, time) inside parked directories and a sidecar `<file>.agflow-disabled` next to parked single files.
  2. `Inventory` treats any item that carries a marker as `enabled=False` **even in a scanned location**, and `_scan_disabled` can rebuild a missing index entry from a marker (this also recovers orphans left by older versions — complements F7).
  3. Doctor: `disabled.marker_leak` WARNING (a marked item sits in a location Antigravity scans: it is *active* despite being "disabled"); `disabled.in_scan_scope` WARNING (a parking path lies under a known discovery root); both evaluated on every doctor run, which is the "periodic check".
* **Files.** `toggles.py`, `inventory.py`, `validate.py`. **Tests:** marker written for skill dir / agent file / rule file; marker survives enable (removed on enable); moved-back marked item is reported disabled + warned; orphan with marker but no index entry is recovered; parking path under a scan root is flagged. Est. ~260 lines.

#### F33 🟡 No contract tests against the real SDK

* **Status.** DRIFTED (§3). Scope = what is missing.
* **Fix.**
  1. `tests/test_sdk_contract.py` + `tests/snapshots/google_antigravity_0.1.20.json`: snapshot of `LocalAgentConfig`/`CapabilitiesConfig` fields, `BuiltinTools` members, the `policy` factory names and the precedence behaviour (F26). Regenerate with `pytest --update-sdk-snapshot`; failures show a readable diff. **Verifiable locally** (SDK installed).
  2. `.github/workflows/sdk-contract.yml`: `schedule` (nightly) + `workflow_dispatch`; installs `agflow[sdk]`, sets `AGFLOW_REQUIRE_SDK=1` (so a missing SDK is a failure, not a skip), runs the contract tests, then — **only if the `GOOGLE_API_KEY` secret is set; otherwise prints a visible notice and exits non-zero for the live step on dispatch** — runs one minimal real graph with `--backend sdk` and diffs `report.json`'s key schema against a committed snapshot.
  3. **Live portion is CI-only and is delivered, not executed, here.** Stated explicitly in FIXLOG and the final report.
* **Files.** as above. Est. ~240 lines.

### Phase 4–5

* **Phase 4** — `tests/test_sync_audits.py` implements S1–S10 as machine checks (details in §7), marked `sync_audit`; they run twice, back to back, and must both be clean. Discrepancies are fixed in their own commits.
* **Phase 5** — G3 end to end, `FIXLOG.md`/`CHANGELOG.md` finalisation, `FINAL-REPORT.md` (§12 format), version proposal (D5).

---

## 7. Sync audits

Each audit is a pytest check in `tests/test_sync_audits.py` (marker `sync_audit`) so it is repeatable and runs in CI; running `pytest -m sync_audit -q` twice is the Phase 4 "clean twice" criterion.

| # | Audit (machine check) | Known sweep items found in discovery |
|---|---|---|
| **S1** | AST scan of `src/` and `scripts/`: string constants equal to or ending in `.gemini`, `skills`, `agents`, `rules`, `workflows`, `plugins`, `hooks.json`, `mcp_config.json` outside `paths.py` (docstrings/comments excluded, explicit allowlist with reasons) | `install.py` bundle literals (`"rules"`, `"skills"`, `"agents"`, `"graphs"`, `"GEMINI.md"`), `fixes.py` (`"rules"`), `scripts/easy_install.py` (`.gemini` in help text) — to be routed through `paths.plugin_layout` or allow-listed |
| **S2** | Schema ↔ consumers: every field of `RunState`/`NodeState` is covered by a round-trip test; ledger event names used in code ⊆ a registry constant; MCP tool signatures ↔ docs (shared with S5) | new events `report_conflict`, `runs_index_rebuilt`, `guard_unavailable` registered |
| **S3** | Hook contract: every `tests/fixtures/hook_payloads/*.json` classifies as declared; README/plugin docs describe only keys present in the vocabulary | – |
| **S4** | No hard-coded tool/model/policy/hook-event names outside `platform_vocab.py` (AST); every bundled agent's `tools:` ⊆ vocabulary; backticked tool identifiers in `GEMINI.md` ⊆ vocabulary ∪ MCP tools | `fixes.py:351`, `backends.py`, `validate.py` |
| **S5** | Each MCP tool signature appears canonically exactly once (graph-run SKILL) and every other mention matches or is bare | F30 |
| **S6** | Every `fetch`/`api(` URL in `static/*.js` has a route in `dashboard/app.py` (method + path pattern), and every `/api` route is used or allow-listed | `stream-ticket` |
| **S7** | install → uninstall round trip leaves the tree identical (global + workspace, with and without profile); `fixes` uses only public install API (AST) | F6, F29 |
| **S8** | Fixtures/goldens: hook payload fixtures, SDK snapshot, vocab golden regenerate cleanly (`--update-*` produces no diff) | currently there are no goldens; new ones added by F31/F32/F33 |
| **S9** | README claims vs measurement: hook-latency statement, test/coverage numbers, requirement list, feature list vs CLI `--help` command names | "about 70 ms" claim |
| **S10** | `FIXLOG.md` has a non-empty "doctor rule" cell for every finding (a rule name, or `none` + reason) | – |

**Per-task sync checklist** (every commit): (1) change the source of truth first; (2) grep all consumers of every changed symbol/path/event/endpoint (SR2) and update them in the same commit; (3) docs, fixtures and goldens ride along (SR3); (4) FIXLOG row added in the same commit; (5) the Sync-Map domains named on the card re-audited at the end of its phase (SR4).

---

## 8. Verification gates per finding

| Gate | Definition | Applied to |
|---|---|---|
| **G1** per fix | New regression test(s) fail on old code (shown by running them against the pre-fix commit via `git stash`-free worktree checkout of the test file only) and pass on new; the touched module's whole test file passes; affected Sync-Map domains audited | every finding |
| **G2** per phase | `pytest -q` fully green; coverage ≥ the previous gate and ≥ 84%; `ruff check src tests scripts` clean; `agflow doctor --path plugin --strict` 0/0; `agflow doctor` on an installed sample workspace 0 errors; S1–S10 clean; **also run on Python 3.10** (`uv run --python 3.10`) at the end of phases 1 and 3 | phases 1–4 |
| **G3** final | G3.1 fresh-clone: `git clone` → `easy-installer.bat --yes` (real) → `doctor` → dashboard boot + stream via ticket → one headless graph run end to end; G3.2 Windows + POSIX parity for F6, F7, F8, F12, F14, F15; G3.3 35/35 closed or waived; G3.4 hook latency flat at 1/50/500 runs and doctor ≤ 2× baseline (it will be far below); G3.5 security re-check (no token in URLs, no unredacted secret outside `state.json`, no `curl\|sh`, no reachable `innerHTML`) | release candidate |

**Platform parity plan (G3.2).** Windows half: run natively on this machine. POSIX half: run the same suite inside WSL `Ubuntu-24.04` (copy of the repo on ext4, `uv sync`, `pytest`) — covers symlink preservation, file-mode preservation, directory fsync and `.bak` POSIX semantics that cannot be exercised here. Anything that still cannot be run (macOS; the Windows-elevated CI path) is stated as unverified.

Finding → gates: F1 G1+G3.1+G3.5 · F2 G1 · F3 G1+G3.4 · F4 G1+G3.1+G3.5 · F5 G1 · F6 G1+G3.2 · F7 G1+G3.2 · F8 G1+G3.2 · F9 G1 · F10 G1(test) · F11 G1+G3.5 · F12 G1+G3.2 · F13 G1 · F14 G1+G3.2 · F15 G1+G3.2 · F16–F19 G1 (+G3.4 for F17) · F20 G1+visual · F21–F26 G1 · F27 G1+G3.4 · F28 G2 · F29 G1+visual · F30 G1+S5 · F31 G1+S3 · F32 G1+S4 · F33 G1 (live part CI-only) · F34 G1 · F35 G1.

---

## 9. R2 / R4 ledgers, holds and approvals

**R2 — behaviour changes visible to users** (none is a break of API/CLI-flag/MCP-signature/on-disk-format/manifest-schema; the two that tighten behaviour are registry-mandated):

| Change | Class | Handling |
|---|---|---|
| F1: `?token=` no longer accepted on `/api/*` | tightening, security | CHANGELOG Security+Changed; header auth unchanged |
| F13: non-tty without `--yes` aborts | tightening, safety | CHANGELOG Changed + migration note (D2) |
| F24b: `ID_RE` on graph input keys | **would break user-authored YAML** | **HELD (D1)**; default warn-then-error |
| F3: new `runs/.index.json` | additive on-disk file | inert for older versions |
| F31: new `hook-health.json`, ledger event, overview field | additive | – |
| F32: optional `vocab.json` override | additive | – |
| F11: ledger/report show `***REDACTED***` for secret-looking input keys | display change, security | `state.json` unchanged |

**R4 — existing tests I will modify** (each stays at least as strict; justification repeated in FIXLOG):

| ID | Test | Change | Proof it was wrong |
|---|---|---|---|
| A1 | `test_dashboard.py::test_auth_and_host_checks` | `params={"token": TOKEN}` → expect 401 | The assertion encodes the exact behaviour registry F1 orders removed. |
| A2 | `test_easy_install.py::test_sh_half_parses[bash]` | pass the script on stdin | Fails at baseline (§2): path handed to the WSL launcher. Same syntax check. |
| A3 | `test_install.py::test_link_install` | skip when symlinks unsupported | Fails at baseline: `WinError 1314`. |
| A4 | `test_install.py::test_agflow_command_prefers_the_running_executable` | launcher name via `os.name` | Fails at baseline: expects POSIX name on Windows; production code is right. |
| A5 | `test_easy_install.py::test_find_wheel_and_requirement` | `time.sleep(0.01)` → `os.utime` | F28 mandates; removes timing dependence. |
| A6 | `test_runner.py::test_interrupt_releases_running_nodes` | `asyncio.sleep(0.2)` → `asyncio.Event` | F28 mandates; removes a race window. |

**Holds / approvals:** D1 (F24b), D4 (any installer download), D5 (version). No push, no PR, no publish, no network download will be performed without being asked.

---

## 10. Self-review against R1–R10

| Rule | Check | Result |
|---|---|---|
| R1 plan first | This file exists and covers every finding before any code change | ✓ |
| R2 no breaks | Every card classified (§9). The one genuine break (F24b) is held; two registry-mandated tightenings are disclosed with migration notes | ✓ with D1 open |
| R3 tests with fixes | Every card lists regression tests; F10 is N/A (characterization test only); F33's live half is CI-only and labelled | ✓ |
| R4 no weakening | Six amendments, each with proof, none loosens an assertion | ✓ |
| R5 root cause | F3 replaces the per-call scan (not a cache around it); F24 fixes template resolution, not just validation; F31 keeps fail-open *and* removes fail-silent; no bare `except: pass` is introduced, and each new `except` names its exception types and records a signal | ✓ |
| R6 atomic commits | One finding per commit, `fix(scope): … (F#)`; `chore` for bench, `docs` for plan/changelog | ✓ |
| R7 platform parity | Windows native + WSL POSIX; Windows-only (F12) and POSIX-only (symlink/mode/dir-fsync) behaviour called out per card; macOS and elevated-Windows CI flagged unverified | ✓ |
| R8 no heavy deps | Only `pytest-cov` (dev). No runtime dependency added; Node is used only by an auto-skipping test | ✓ |
| R9 scope | Widenings are explicit and justified: F5b (same function, same root cause), F11 report artifacts (same sink class), F13 sh half (same pattern, same file F4 rewrites), F28 CI matrix (needed to verify R7). Everything else in §11 is *logged, not fixed* | ✓ |
| R10 fail loud | Every new error path names its signal: F5→doctor `load`; F2→`report_conflict`; F3→`runs_index_rebuilt`/doctor; F6→`install.interrupted`; F7→`disabled.stale`; F31→debug+counter+ledger+dashboard+doctor; F32→`vocab.override.invalid`; F35→`SyncError`; F16→`McpError` | ✓ |

**Plan-level risks.** (1) F3 and F6 touch hot/core paths — mitigated by crash-injection and equivalence tests. (2) Windows-only behaviour (F12, install rename locks) can be verified only on this machine, and POSIX-only behaviour only in WSL. (3) The uv installer hashes depend on GitHub release metadata being accurate (D4). (4) F31's "real payload samples" cannot be obtained from here; they will be honestly labelled.

---

## 11. Observations outside the registry

Logged for the final report; **not** fixed unless they block a registry fix (§11 of the prompt).

* **N1.** `store.register_run` swallows `OSError` (`store.py:168`) with a comment — the same silent pattern R5 warns about. The disk index is "an optimisation", but a permanently unwritable index is invisible.
* **N2.** `hooks._debug_log` swallows `OSError` (`hooks.py:266`); F31 covers the *alarm* path but not logging failures in general.
* **N3.** `agflow migrate` / `POST /api/migrate` move and rename user files with no backup or undo (by design; `.bak` rename only).
* **N4.** `ID_RE` permits `-` in node/input ids, but Jinja reads `{{ inputs.bug-id }}` as subtraction; ids with hyphens can only be reached via `inputs['bug-id']`. (Interacts with D1.)
* **N5.** The README's "about 70 ms per hook" cannot be reproduced on this machine even at one run (≈550 ms wall, interpreter start-up bound). F3 replaces it with a measured, run-count-independent statement; the absolute number is hardware-dependent and will be labelled as such.
* **N6.** `google.antigravity` may have no wheels for some platforms (the installer already handles this); on this Windows machine the SDK *is* installed, which differs from the prompt's assumption.
