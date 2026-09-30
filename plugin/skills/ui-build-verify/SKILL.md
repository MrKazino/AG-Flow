---
name: ui-build-verify
description: Build or change user-interface features and verify them in a real browser with Antigravity's browser agent — run the dev server, exercise the user flows, check console/network errors, responsive layouts and states, and capture screenshots as evidence. Use for any frontend feature, UI bug or "make it look/work like X" request.
---

# UI build & verify

## Procedure
1. **Understand the UI stack**: framework (React/Next, Vue/Nuxt, Svelte, Angular, plain), styling (Tailwind, CSS modules, design system), routing, state, and how to run it (`package.json` scripts, README). Find an existing similar component to copy patterns from.
2. **Define the flows and states** from the request: happy path, loading, empty, error, disabled, long content, permissions. Write them down as acceptance checks.
3. **Implement** with the design system's components and tokens; semantic HTML; handle every state explicitly; keep components small. Add/extend component or e2e tests if the project has them (Testing Library, Playwright, Cypress).
4. **Run the app.** Start the dev server in the background (e.g. `npm run dev`), then poll until it responds (`curl -sf http://localhost:<port>` in a short loop) — don't blindly sleep.
5. **Verify in the browser** (browser subagent):
   - navigate to the page; perform each flow with real clicks/typing;
   - check the console for errors/warnings and the network panel for failed requests;
   - resize to 375, 768 and 1280 px wide; confirm no overflow or overlap;
   - tab through the page: focus visible and in logical order; Enter/Space activate controls;
   - capture a screenshot per key state to `.agents/agflow/evidence/<date>-<slug>/`.
6. **Fix and repeat** until all checks pass. Run unit/e2e tests, linter and typecheck.
7. **Clean up**: stop the dev server you started; remove temporary debug code.

## Quality bar
- No console errors; no layout shift on load; images sized; fonts don't flash invisible.
- Accessible names on all controls; contrast meets WCAG AA (see `a11y-audit`).
- Works with keyboard only; respects reduced-motion.

## Exit criteria
Summary lists each flow/state checked with pass/fail and the screenshot paths (as artifacts), plus test/lint results.
