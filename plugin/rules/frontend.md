---
trigger: glob
globs: "**/*.tsx,**/*.jsx,**/*.vue,**/*.svelte,**/*.astro,**/*.css,**/*.scss,**/*.html"
description: Standards for UI code.
---

# Frontend standards

- Semantic HTML first (`button`, `nav`, `main`, `label`); ARIA only when no native element fits.
- Every interactive element is keyboard reachable with a visible focus style; every input has a label; images have meaningful `alt` (or `alt=""` if decorative).
- Colour contrast ≥ 4.5:1 for body text (3:1 for large text and UI components). Don't convey meaning by colour alone.
- Responsive from 320px up; no horizontal scroll; respect `prefers-reduced-motion` and `prefers-color-scheme` where the app supports themes.
- Reuse the design system's components and tokens (spacing, colour, typography) instead of one-off values.
- Avoid layout shift: reserve space for images/async content; lazy-load below-the-fold media.
- State: keep it close to where it's used; derive instead of duplicating; handle loading, empty and error states explicitly.
- **Verify in the browser** before claiming done: load the page, exercise the flow, check the console for errors, and capture a screenshot as evidence.
