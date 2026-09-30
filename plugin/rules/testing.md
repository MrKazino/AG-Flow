---
trigger: glob
globs: "**/*test*.*,**/*spec*.*,**/tests/**,**/__tests__/**,**/test/**"
description: Standards for test files.
---

# Testing standards

- Test behaviour through the public interface, not implementation details.
- One reason to fail per test; name tests after the behaviour (`rejects_expired_token`, not `test_3`).
- Arrange–Act–Assert; keep fixtures small and local; prefer real objects over mocks, mock only at process boundaries (network, clock, filesystem when slow, randomness).
- Deterministic: no sleeps (use fake clocks / polling with timeouts), no real network, seeded randomness, no order dependence.
- Every bug fix adds a regression test that fails without the fix — show it failing, then passing.
- Cover edge cases: empty, null/None, boundary values, unicode, large inputs, concurrency where relevant, error paths.
- Never delete, skip or loosen a failing test to get green; fix the cause or report it.
- Run the narrowest relevant test first, then the full suite before hand-off; quote the result line.
