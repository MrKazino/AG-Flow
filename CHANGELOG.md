# Changelog

All notable changes to agflow are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project aims to follow [Semantic Versioning](https://semver.org/). Finding IDs (`F1`…`F35`) refer to the
audit registry tracked in [PLAN.md](PLAN.md) and [FIXLOG.md](FIXLOG.md).

## [Unreleased] — audit remediation

### Security
_(entries are added with each fix)_

### Changed
_(entries are added with each fix)_

### Fixed
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
