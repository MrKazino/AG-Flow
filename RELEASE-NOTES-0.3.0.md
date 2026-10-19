## AG-Flow 0.3.0

An audit-driven release: 35 findings from a review of 0.2.0 are closed (34 fixed, one not reproducible and locked by a
test), plus a follow-up that found why agflow's hooks never ran in a real Antigravity session. Almost everything is a
fix; there are no new commands except what `agflow fix` and `agflow doctor` now report.

### Please read before upgrading
- **Run `agflow install` (or `agflow fix`) once after upgrading.** Antigravity ignored the `hooks.json` that 0.2.0
  wrote (only the `Stop` entry ran), so the safety guard and the run ledger never ran in practice. The file is now in the
  layout Antigravity documents and every hook command fails open. After the reinstall **the guard runs in every
  session**: it asks before a force-push, `git reset --hard`, `curl … | sh` or reading `.env` files, and denies things
  like `rm -rf /`. `agflow install --no-hooks` or `AGFLOW_GUARD=off` switches that off. (An Antigravity CLI older than
  1.0.16 rejects the empty answer a hook gives when it has no opinion: use `--no-hooks` there.)
- Dashboard clients must send the token in the `X-Agflow-Token` header; `?token=` is rejected (F1).
- Unattended runs of `easy-installer` must pass `--yes` (F13).
- A graph that lists the same dependency twice in `depends_on` no longer loads (F24a).
- The dashboard needs a browser from 2024 or later (F29).
- Graph input names that do not follow the node-id style (`Feature`, `bugID`) still work and now get a doctor warning;
  agflow 0.4.0 will reject them (F24b).

### Highlights
- **A broken agflow install can no longer fail your agent.** A stale `agflow.exe` (`ModuleNotFoundError: No module
  named 'agflow'`) used to make Antigravity report a failed `Stop` hook and end the turn. Hook commands now fall back to
  `{}` with exit code 0, and `agflow doctor` runs the installed hooks and reports `hooks.broken` so it is not silent.
- **Hooks are much cheaper**: a call with no running graph no longer loads the run store or a YAML parser
  (about 0.2 s instead of 0.3–0.65 s on Windows, about 0.05 s instead of 0.2 s on Linux, on the development machine).
- **Crash-safe installs and edits**: atomic, symlink-safe writes (F14), a journalled plugin swap that survives a crash
  (F6), journalled enable/disable with markers that survive moves (F7, F34), collision-free backups (F8, F15).
- **Security**: the dashboard token no longer travels in a URL (F1), no way to turn text into markup in the dashboard
  (F25), credential-looking graph inputs are redacted from ledgers and reports (F11), the uv bootstrap is pinned and
  checksum-verified (F4).
- **Speed and scale**: `agflow doctor` on a 350-item plugin 63 s → 1.3 s (F27); hook and status lookups no longer grow
  with the number of runs (F3, F17).
- **Honest diagnostics**: the guard records every payload it could not read (F31), Antigravity's names live in one
  versioned, overridable table (F32), `sync gemini-cli` checks the files it writes into (F35).
- **Undo for `agflow migrate`** (it now goes through the same backup history as `agflow fix`), a visible record when
  the global run index cannot be written, and the headless runner no longer leaves a node `running` when it cannot
  record its result.

The full list, with the evidence behind each change, is in [CHANGELOG.md](CHANGELOG.md) and [FIXLOG.md](FIXLOG.md).

### Verification
893 tests pass on Windows (Python 3.14) and on Ubuntu 24.04 (Python 3.12), coverage 89 %, `ruff` clean, the S1–S10 sync
audits pass. The sdist and wheel build, the wheel installs into a clean environment, `agflow install` and `agflow doctor`
(with the hook probe) run clean from it, and the 0.2.0 failure was reproduced against it: with the package missing the
hook exits 1 without the wrapper and `0` with `{}` with it.

Not exercised here: the new Windows/macOS CI jobs, the nightly SDK contract job, a real model run through the SDK
backend, and a live Antigravity session (so "the guard now runs in the app" rests on Antigravity's documentation, its
CLI log and its engine strings).

### Install / upgrade
Easiest: download this release's source, extract it to a fresh folder and run `easy-installer.bat` (Windows) or
`sh easy-installer.bat` (Linux, macOS, WSL) from the folder that holds `pyproject.toml`; it installs the `agflow`
command and the plugin and runs `agflow doctor`. By hand:
```bash
uv tool install --force --reinstall "agflow[sdk] @ git+https://github.com/MrKazino/AG-Flow@v0.3.0"
agflow install
agflow doctor
```
