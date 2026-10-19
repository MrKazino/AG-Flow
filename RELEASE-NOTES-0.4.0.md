## AG-Flow 0.4.0

AG-Flow now keeps itself in step with Antigravity, updates itself only when you say so, checks what agents claim against
the workspace, learns from each workflow's runs and has a manager panel inside Antigravity. Anything that changes a
workflow, a file you own or the installed program waits for your approval, is backed up and can be undone.

### Please read before upgrading
- **Run `agflow install` once after upgrading.** It writes the two new sidecars, the new skills and the `verifier`
  subagent, rewrites `plugin.json` and records which plugin files are yours so that later updates keep your edits.
- **The sidecars are off until you switch them on**, and AG-Flow never does it for you: `agflow panel status` says where
  each one stands and prints the steps.
- **The bundled graphs now require evidence** on the steps that declare outputs: a step that reports success without its
  declared files goes back to the agent. Your own graphs keep the default (`advisory`) until you set `evidence:`.
  `agflow graph run --backend fake` writes placeholders for declared outputs, so a dry run still finishes, but it needs a
  workspace to write into.
- `plugin.json` keeps only the fields Antigravity reads; `author`, `license`, `homepage` and `keywords` were dropped
  silently by Antigravity anyway.
- Graph input names that do not follow the node-id style (`Feature`, `bugID`) are still only a doctor warning; the
  rejection announced for 0.4.0 moves to 0.5.0.

### Highlights
- **`agflow sync`: AG-Flow notices when Antigravity changes.** It reads the installed Antigravity and the documentation
  that ships with it and compares the hook events, manifest and sidecar fields and size limits AG-Flow depends on with
  what this release was verified against: `ok`, `drift`, `breaking` or `unknown`, naming the affected component. Only
  hashes and short identifiers are kept, never the vendor's text. `agflow sync apply` accepts a newer Antigravity only
  after AG-Flow's own doctor and live hook probe pass.
- **`agflow update`: updates you approve.** From the GitHub Releases of this repository only (the name `agflow` on PyPI is
  not this project). The wheel must match `SHA256SUMS`, is inspected before it is installed, and the new version's
  doctor runs on its own bundled plugin first. Then a backup, the install, your edited plugin files put back on top, the
  new doctor and hook probe; any failure restores the backup, and `agflow update rollback` restores it later.
- **Fewer guesses.** Every agent step is checked against the workspace before it counts as done (declared files exist and
  are not empty, declarative content checks hold); a read-only `verifier` agent re-checks what other agents claimed;
  `GEMINI.md` has a grounding protocol (a claim needs a source that was opened or a command that was run; reports label
  what is verified, inferred or unknown). This makes an unsupported claim visible and harder to pass on; it does not
  make a model infallible.
- **Workflows learn, you decide.** Per workflow, AG-Flow records what went wrong in finished runs and feeds the lessons
  that recurred into the next dispatch of the affected step. `agflow improve` turns a recurring problem into a proposal
  with the evidence and the exact diff; nothing is applied until you apply it, and it can be undone.
- **A manager inside Antigravity.** Antigravity's Settings screen is not an extension point, so AG-Flow ships what is: a
  side-pane panel (`sidecar://agflow/manager/`) with a Workflows page (what each workflow needs and where it was found,
  runs, lessons, proposals) and a Sync & updates page, plus a scheduled sidecar that runs `agflow maintenance run` once
  a day (checks only; no model, no conversation).
- **Faster start-up** (Windows 11, Python 3.14, median of 5 fresh interpreters; yours will differ): `agflow version`
  560 → 94 ms, `agflow doctor` 3167 → 1746 ms, an idle hook 193 → 179 ms. A dashboard page on a workspace of 450 skills, agents and rules loads about twice as fast
  after the first call (overview 871 → 429 ms). `scripts/bench.py` reproduces the figures.

The full list, with the evidence behind each change and what was not verified, is in [CHANGELOG.md](CHANGELOG.md),
[FIXLOG.md](FIXLOG.md) (H5–H10) and [docs/research-antigravity-0.4.0.md](docs/research-antigravity-0.4.0.md).

### Verification
1,470 tests pass on Windows (Python 3.14, 10 skipped, coverage 92 %, floor 88 %) and 1,473 on Ubuntu 24.04 under WSL2
(Python 3.12, 7 skipped); `ruff` is clean; the S1–S10 sync audits pass; `agflow doctor --path plugin --strict` reports
nothing; all five bundled graphs validate and run to success on the fake backend (with `evidence: required`) in a
sandboxed home. `scripts/make_release.py` builds the wheel, sdist, `SHA256SUMS` and `release.json` and re-checks them as
the updater would; `scripts/smoke_release.py` installs real wheels into a throwaway environment with a sandboxed home
and passes 16 of 16 checks: a release that cannot start is refused with nothing changed, the update succeeds, an edited
plugin file survives it and the rollback.

Not exercised here, and said plainly:
- **A live Antigravity window.** The panel's handshake follows the vendor's own sidecar runtime and was verified in a
  browser, not inside Antigravity; whether Antigravity opens the chat link as documented is untested.
- **An update from github.com.** No release existed when this was written, so the feed was exercised against a local
  fake of the GitHub API. The first real update works once a release exists and a later one is published.
- **The effect on hallucination.** The evidence check, the verifier and the grounding protocol make an unsupported
  claim visible and harder to pass on; no live model was run to measure how often one occurs.
- The documentation Antigravity ships was available for CLI 1.3.3 only.

### Install / upgrade
Easiest: download this release's source, extract it to a fresh folder and run `easy-installer.bat` (Windows) or
`sh easy-installer.bat` (Linux, macOS, WSL) from the folder that holds `pyproject.toml`. By hand:
```bash
uv tool install --force --reinstall "agflow[sdk] @ git+https://github.com/MrKazino/AG-Flow@v0.4.0"
agflow install
agflow doctor
agflow panel status
```
From 0.4.0 on, `agflow update check` tells you when a newer release is published.
