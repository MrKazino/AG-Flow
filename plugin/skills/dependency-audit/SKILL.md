---
name: dependency-audit
description: Audit third-party dependencies for known vulnerabilities (CVEs), outdated or abandoned packages, risky install scripts and license conflicts across npm/pnpm/yarn, pip/uv/poetry, Go, Cargo, Maven/Gradle and more, then propose safe upgrades. Use before releases, when adding dependencies, or when asked about CVEs, supply-chain risk or licenses.
---

# Dependency audit

## Procedure
1. **Detect ecosystems and run the audits** with the bundled helper (read-only, prints JSON):
   ```bash
   python3 scripts/audit.py --root . --json
   ```
   It detects manifests/lockfiles and runs the matching tool when installed: `npm audit --json`, `pnpm audit --json`, `yarn npm audit --json`, `pip-audit -f json`, `govulncheck -json ./...`, `cargo audit --json`, `bundle audit`, `composer audit`. Missing tools are reported, not installed.
2. **If a tool is missing**, suggest the install command to the user (e.g. `pipx install pip-audit`, `go install golang.org/x/vuln/cmd/govulncheck@latest`) — don't install globally without permission.
3. **Triage each vulnerability**: is the vulnerable code path reachable in this project (direct vs transitive, dev vs prod dependency, used function)? Is a fixed version available? Does the upgrade cross a major version?
4. **Hygiene checks**: lockfile committed; versions pinned; no packages unmaintained > 2 years for critical paths; no typosquat-looking names; review `postinstall`/`preinstall` scripts of new packages.
5. **Licenses**: list licenses of direct dependencies (e.g. `npx license-checker --summary`, `pip-licenses`, `go-licenses report ./...`). Flag copyleft (GPL/AGPL/LGPL) or unknown licenses for proprietary projects.
6. **Remediate (if in scope)**: smallest safe upgrade per package (patch → minor → major), one ecosystem at a time; update lockfile with the project's package manager; run the full test suite after each batch.

## Report
| Package | Installed | Fixed in | Severity | Direct? | Reachable? | Action |
|---|---|---|---|---|---|---|
Plus: tools run and versions, license findings, recommended upgrade plan, residual risk.

## Exit criteria
`succeeded` when no critical/high vulnerabilities remain unaddressed (fixed, or documented as unreachable with justification); otherwise `rejected` with the list.
