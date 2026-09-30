---
name: security-audit
description: Audit an application or change for exploitable vulnerabilities — threat model, OWASP Top 10 classes, authn/authz, secrets, injection, SSRF, unsafe deserialisation, misconfiguration — and produce a ranked, evidence-backed report with fixes. Use for security reviews, pre-release checks, or after changes to auth, input handling, file/network access or crypto.
---

# Security audit

## Procedure
1. **Scope and assets.** What's in scope (paths, services)? What must be protected (user data, credentials, money, availability)? Who are the adversaries (anonymous internet, authenticated user, insider)?
2. **Map the attack surface.** Enumerate entry points: HTTP routes (`grep` for route decorators/routers), CLI args, message consumers, file uploads/parsers, webhooks, scheduled jobs, admin endpoints. Mark trust boundaries and where authn/authz is enforced.
3. **Threat model (STRIDE, briefly).** For each entry point: Spoofing, Tampering, Repudiation, Information disclosure, Denial of service, Elevation of privilege.
4. **Hunt** using the checklist in `references/checklist.md`. Useful searches:
   - injection: string-built SQL / `exec(` / `eval(` / `subprocess` with `shell=True` / `child_process.exec` / template `|safe` / `dangerouslySetInnerHTML`
   - secrets: `grep -rEn "(api[_-]?key|secret|token|password)\s*[:=]" --include=*.{py,js,ts,go,java,yml,yaml,json,env}`; run `gitleaks detect` or `trufflehog` if installed
   - deserialisation: `pickle.loads`, `yaml.load(` without SafeLoader, `ObjectInputStream`, `unserialize(`
   - SSRF / path traversal: user-controlled URLs or paths reaching `requests`/`fetch`/`open`
5. **Run available scanners** (don't install system-wide without asking): `semgrep --config auto`, `bandit -r`, `npm audit`, `gosec ./...`, and the `dependency-audit` skill for third-party packages.
6. **Confirm** each finding by tracing data flow from source to sink; write a minimal proof (test or request) when safe and local. Mark unconfirmed ones as *suspected*.
7. **Report** (below). For remediation tasks, fix critical/high first, add a regression test per fix, and re-run the hunt for siblings.

## Report format (`docs/security/<date>-audit.md` unless told otherwise)
| # | Severity | Class | Location | Status |
|---|---|---|---|---|
Then per finding: description · exploit scenario · evidence · fix · references (CWE/OWASP).
End with: scope covered, tools run (with versions), what was *not* covered.

## Severity guide
**Critical**: remote, unauthenticated compromise or mass data exposure. **High**: authenticated privilege escalation, significant data exposure, RCE with preconditions. **Medium**: limited exposure, requires user interaction or unusual config. **Low**: defence-in-depth, hardening.

## Rules
Never print real secret values — cite file and line only. Never attack external systems. Stay read-mostly unless remediation is in scope.

## Exit criteria
Report saved; summary lists counts per severity and the top 3 risks. `rejected` if any critical/high remains open.
