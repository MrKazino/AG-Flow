---
name: visual-regression
description: Detect unintended visual changes in a web UI by capturing screenshots of key pages and states at fixed viewports and comparing them with approved baselines (Playwright toHaveScreenshot, Storybook/Chromatic, or pixel diffs), then report or update baselines deliberately. Use after CSS/layout/component changes, dependency upgrades of UI libraries, or when asked for visual testing.
---

# Visual regression

## Procedure
1. **Choose the mechanism** the project already uses; otherwise default to Playwright:
   - Playwright: `await expect(page).toHaveScreenshot('name.png', { fullPage: true, maxDiffPixelRatio: 0.01 })`; run `npx playwright test`; baselines live next to the spec in `*-snapshots/`.
   - Storybook: `test-storybook` with image snapshots, or Chromatic if configured.
   - No framework: capture screenshots with the browser agent and compare with an image diff (e.g. `pixelmatch`, ImageMagick `compare -metric AE`).
2. **Make captures deterministic**: fixed viewport(s) (375×812, 1280×800), `deviceScaleFactor: 1`, fixed locale/timezone, seeded/mocked data, disable animations (`animations: 'disabled'`, `prefers-reduced-motion`), wait for fonts and network idle, mask dynamic regions (timestamps, avatars, ads).
3. **Define coverage**: key pages × important states (empty, loading, error, long text, hover/focus where meaningful), light/dark theme if supported.
4. **Run the comparison** against baselines. For each diff, open the actual/expected/diff images and classify:
   - **intended** (matches the requested change) → update that baseline only (`npx playwright test --update-snapshots -g "<test>"`);
   - **regression** → report with the diff image path and the likely cause (CSS selector/component).
5. **Never bulk-update baselines** to make a run pass. Baseline updates are part of the diff and must be justified.

## Exit criteria
Table of pages/states compared with result (match / intended change / regression) and diff image paths as artifacts. `rejected` if any unexplained regression remains.
