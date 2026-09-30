---
name: incident-triage
description: Triage a production incident or outage — assess impact, stabilise first (rollback, feature flag, scale), then find the root cause from logs, metrics, traces and recent changes, and write a blameless post-incident review with follow-ups. Use when something is broken in production, error rates or latency spike, or a user reports an outage.
---

# Incident triage

Order of priorities: **stop the bleeding → understand → fix → learn.**

## Procedure
1. **Assess (first 5 minutes).** What is broken, for whom, since when? Severity: SEV1 (outage/data loss/security), SEV2 (major degradation), SEV3 (minor). Start a timeline (UTC timestamps) and keep it updated.
2. **Stabilise before diagnosing.** Candidate mitigations, least risky first: roll back the most recent deploy/config change · disable the feature flag · scale up/out · fail over · rate-limit/shed load · block the abusive client. Any action on production requires explicit user approval — propose the exact command and its rollback.
3. **Correlate with change.** Recent deploys, config changes, dependency upgrades, cron jobs, traffic shifts, upstream provider status. `git log --since="2 hours ago"`, deploy history, feature-flag audit logs.
4. **Gather evidence**: error logs (group by message/fingerprint), metrics (error rate, latency percentiles, saturation: CPU/memory/connections/queue depth), traces for failing requests. Quote the evidence; don't paraphrase numbers.
5. **Hypothesise and test** (see `debug-systematic`): 2–3 ranked causes, each with a discriminating check. Prefer read-only checks in production.
6. **Fix** in a normal change with tests; deploy through the usual pipeline once approved. Confirm recovery with the same metrics that showed the problem.
7. **Post-incident review** (`docs/incidents/<date>-<slug>.md`), blameless: summary · impact (users, duration, data) · timeline · root cause and contributing factors · what went well/badly · action items with owners and priority (detection, prevention, mitigation).

## Rules
- Communicate early and on a cadence: what's known, what's being done, next update time.
- Never run destructive commands in production to "see what happens".
- Protect user data and secrets in all notes and logs you share.

## Exit criteria
Current status (mitigated/resolved), evidence of recovery, root cause (or best hypothesis with confidence), and the post-incident document path.
