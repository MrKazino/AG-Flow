---
trigger: always_on
description: Security baseline for all code and commands.
---

# Security baseline

- Treat all external input (HTTP, CLI args, files, env, LLM output) as untrusted: validate type, size and format at the boundary.
- Use parameterised queries / ORM bindings; never build SQL, shell commands or HTML by string concatenation with untrusted data.
- No `eval`/`exec`/`pickle.loads`/`yaml.load` (unsafe loader) on untrusted data. Use safe parsers.
- Secrets come from environment variables or a secret manager — never hard-coded, logged, or committed. Keep `.env` in `.gitignore`.
- Least privilege for tokens, IAM roles, containers (non-root user) and file permissions.
- Authentication and authorisation are checked server-side on every request; deny by default.
- Pin dependency versions and prefer maintained, widely used packages; flag new dependencies in your report.
- Shell: quote variables, avoid `curl | sh`, never run commands you don't understand; prefer dry-run flags first.
- If you find a vulnerability outside your task, report it as a follow-up with severity instead of silently fixing unrelated code.
