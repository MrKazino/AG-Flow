# Security audit checklist

## Access control (OWASP A01)
- [ ] Every non-public route checks authentication server-side.
- [ ] Authorisation checks the *object* (IDOR): user can only access their own records.
- [ ] Admin/debug endpoints disabled or protected in production.
- [ ] CORS allowlist is explicit; no `*` with credentials.

## Cryptography (A02)
- [ ] TLS everywhere; HSTS on web apps.
- [ ] Passwords hashed with argon2id/bcrypt/scrypt (never plain SHA/MD5).
- [ ] No home-made crypto; keys from a KMS/secret store; random values from a CSPRNG.

## Injection (A03)
- [ ] SQL via parameters/ORM; no string concatenation with user data.
- [ ] Shell commands avoid `shell=True`; arguments passed as arrays; inputs allowlisted.
- [ ] Templates auto-escape; no `|safe`/`dangerouslySetInnerHTML` on user data.
- [ ] NoSQL/LDAP/XPath queries built safely.

## Insecure design (A04)
- [ ] Rate limits on auth, password reset and expensive endpoints.
- [ ] Business-logic abuse considered (negative quantities, replay, race conditions on balances).

## Misconfiguration (A05)
- [ ] Debug mode off in production; stack traces not returned to clients.
- [ ] Security headers: CSP, X-Content-Type-Options, frame-ancestors/X-Frame-Options, Referrer-Policy.
- [ ] Containers run as non-root; minimal base images; no secrets baked into images.

## Vulnerable components (A06)
- [ ] Dependency audit clean for critical/high (see `dependency-audit`).
- [ ] Lockfiles committed; versions pinned.

## Authentication (A07)
- [ ] Session cookies `HttpOnly`, `Secure`, `SameSite`; sessions rotate on login and expire.
- [ ] MFA available for privileged accounts; brute-force protection.
- [ ] JWT: algorithm pinned (no `none`), signature verified, `exp` checked, secrets strong.

## Integrity (A08)
- [ ] No unsafe deserialisation of untrusted data.
- [ ] CI/CD: pinned actions (by SHA), least-privilege tokens, protected branches.

## Logging & monitoring (A09)
- [ ] Security events logged (auth failures, permission denials) without secrets/PII.
- [ ] Alerts exist for anomalous patterns.

## SSRF (A10)
- [ ] User-supplied URLs validated against an allowlist; internal/metadata IPs blocked; redirects re-validated.

## Files & paths
- [ ] Uploads: size limits, type validation by content, stored outside web root, random names.
- [ ] Path joins normalised and confined to a base directory.

## Secrets
- [ ] No secrets in repo history (`gitleaks detect`), config files or logs; `.env` git-ignored.

## LLM / agent features (if present)
- [ ] Prompt injection considered: untrusted content can't trigger privileged tools without confirmation.
- [ ] Model output treated as untrusted input before execution or rendering.
