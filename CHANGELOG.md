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
- **Discovery survives corrupt files (F5).** One bad entry in the disabled-items index (invalid JSON, unknown kind,
  missing key, non-UTF-8 bytes) or an unreadable/corrupt plugin manifest, `mcp_config.json`, `hooks.json` or
  `GEMINI.md` no longer crashes `doctor`, the dashboard or the hooks. Each problem is reported by `agflow doctor`
  as a `load` error naming the file and entry; valid neighbouring entries still load.
- **Atomic writes are now durable and identity-preserving (F14).** Every file agflow writes (configs, manifests,
  run state) is fsynced before it replaces the old one and the directory is fsynced afterwards; writing to a
  symlinked file (for example a linked dotfile) updates the link target instead of replacing the link; an
  existing file keeps its permission bits.
