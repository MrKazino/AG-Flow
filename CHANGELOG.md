# Changelog

All notable changes to agflow are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project aims to follow [Semantic Versioning](https://semver.org/). Finding IDs (`F1`…`F35`) refer to the
audit registry tracked in [PLAN.md](PLAN.md) and [FIXLOG.md](FIXLOG.md).

## [Unreleased] — audit remediation

### Security
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
- **`?token=` is no longer accepted on `/api/*` (F1; migration).** Scripts that called the dashboard API with the
  token in the query string must send it as the `X-Agflow-Token` header instead. Browser use is unaffected.

### Fixed
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
