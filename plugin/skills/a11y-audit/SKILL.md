---
name: a11y-audit
description: Audit and fix web accessibility against WCAG 2.2 AA — semantics, keyboard access, focus management, labels, contrast, alt text, ARIA misuse, motion — using automated scanners plus manual keyboard and screen-reader-oriented checks in the browser. Use for accessibility reviews, before UI releases, or when asked about a11y, WCAG or screen readers.
---

# Accessibility audit (WCAG 2.2 AA)

## Procedure
1. **Automated scan** (catches ~30–40% of issues): run what the project has or can use without global installs —
   `npx @axe-core/cli <url>` · `npx lighthouse <url> --only-categories=accessibility --output=json` · `npx pa11y <url>` · eslint `jsx-a11y` for React. Record violations by rule and count.
2. **Keyboard pass** in the browser: Tab/Shift+Tab through every interactive element; check visible focus, logical order, no traps; Enter/Space/Escape/arrow keys behave as expected for each widget (menus, dialogs, tabs, comboboxes).
3. **Semantics & names**: landmarks (`header/nav/main/footer`), one `h1` and a sane heading outline, lists and tables marked up correctly, every control has an accessible name (label, `aria-label`, or text), form errors linked via `aria-describedby`.
4. **Visual**: text contrast ≥ 4.5:1 (3:1 large text & UI components); zoom to 200% and 400% (reflow at 320 CSS px) without loss; colour isn't the only signal; target size ≥ 24×24 px.
5. **Dynamic content**: dialogs trap and restore focus; live regions announce async updates; route changes move focus or announce the page.
6. **Media & motion**: meaningful `alt` text (empty for decorative), captions for video, `prefers-reduced-motion` honoured, nothing flashes > 3 times/second.
7. **Fix** (if in scope) starting with blockers for keyboard and screen-reader users; prefer native elements over ARIA. Re-run the scan and keyboard pass.

See `references/wcag-quick.md` for the success criteria most often failed.

## Report
| Issue | WCAG SC | Severity | Location (page/component) | Fix |
|---|---|---|---|---|
Severity: **blocker** (task impossible for some users), **serious**, **moderate**, **minor**.

## Exit criteria
Scanner results before/after, keyboard pass result, list of fixed and remaining issues. `rejected` if blockers remain.
