# WCAG 2.2 AA — most frequently failed success criteria

| SC | Name | Quick check |
|---|---|---|
| 1.1.1 | Non-text content | Informative images have meaningful `alt`; decorative `alt=""`; icon buttons have accessible names. |
| 1.3.1 | Info and relationships | Headings, lists, tables, labels expressed in markup, not just styling. |
| 1.3.5 | Identify input purpose | `autocomplete` attributes on personal-data inputs. |
| 1.4.3 | Contrast (minimum) | 4.5:1 text, 3:1 large text (≥ 24px or ≥ 18.66px bold). |
| 1.4.10 | Reflow | No horizontal scroll at 320 CSS px width (400% zoom). |
| 1.4.11 | Non-text contrast | 3:1 for UI component boundaries, focus indicators, icons conveying meaning. |
| 1.4.12 | Text spacing | Layout survives increased line/letter/word spacing. |
| 2.1.1 | Keyboard | Everything operable by keyboard; no mouse-only handlers. |
| 2.1.2 | No keyboard trap | Focus can always move away (except modal dialogs, which must be closable). |
| 2.4.3 | Focus order | Order follows meaning; dialogs move focus in and restore it on close. |
| 2.4.7 | Focus visible | A clearly visible focus indicator on every focusable element. |
| 2.4.11 | Focus not obscured (min) | Sticky headers/footers don't hide the focused element. |
| 2.5.7 | Dragging movements | A single-pointer alternative exists for drag interactions. |
| 2.5.8 | Target size (min) | Targets ≥ 24×24 CSS px or sufficiently spaced. |
| 3.3.1 | Error identification | Errors described in text, linked to the field. |
| 3.3.2 | Labels or instructions | Every input has a visible label. |
| 3.3.8 | Accessible authentication | No cognitive tests (e.g. transcribing) without an alternative; allow paste in password fields. |
| 4.1.2 | Name, role, value | Custom widgets expose correct role/state (`aria-expanded`, `aria-selected`, …). |
| 4.1.3 | Status messages | Async results announced via `role="status"`/`aria-live` without moving focus. |
