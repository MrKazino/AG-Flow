---
name: release-notes
description: Prepare a release — determine the semantic version bump from the changes, generate a grouped changelog and human-readable release notes from commits and PRs, update version files, and draft the tag/release for approval. Use when asked for a changelog, release notes, version bump, or to cut or prepare a release.
---

# Release notes & versioning

## Procedure
1. **Find the range**: last release tag (`git describe --tags --abbrev=0`) to `HEAD`, or the range you were given.
2. **Collect changes** with the bundled helper (groups Conventional Commits, flags breaking changes, suggests the bump):
   ```bash
   python3 scripts/changelog.py --since <last-tag> --markdown
   python3 scripts/changelog.py --since <last-tag> --json   # machine-readable
   ```
   Non-conventional commit messages land in "Other changes"; rewrite them into user-facing language.
3. **Decide the version** (SemVer): breaking change → major (minor while `0.x`), feature → minor, fixes only → patch. Respect the project's existing scheme (CalVer, pre-releases).
4. **Write the notes for users**, not for the git log: highlights first, then Breaking changes (with migration steps), Features, Fixes, Performance, Security, Deprecations, Internal. Link PRs/issues when the repo host is known. Credit contributors if the project does.
5. **Update files** the project uses: `CHANGELOG.md` (Keep a Changelog format), version fields (`package.json`, `pyproject.toml`, `Cargo.toml`, `version.go`…). Run the build/tests after bumping.
6. **Draft, don't publish.** Prepare `git tag -a vX.Y.Z -m "..."` and the release text. Pushing tags, publishing packages or creating the hosted release requires explicit user approval.

## Exit criteria
Proposed version with rationale, the changelog section, updated files, and the exact (approval-gated) tag/publish commands.
