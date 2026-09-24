# Ready-to-List design system (MASTER)

Derived from the UI/UX Pro Max skill's design database
(https://github.com/nextlevelbuilder/ui-ux-pro-max-skill, data/products.csv, colors.csv, typography.csv, styles.csv),
rows matched on: government, public service, legal, court, dashboard.

## Product match
| Field | Value (from the database) |
|---|---|
| Product types | Government/Public Service (#13), Legal Services (#40) |
| Primary style | Accessible & Ethical + Minimalism & Swiss Style |
| Dashboard style | Executive Dashboard: high-level KPIs, large key metrics, trend indicators, at-a-glance |
| Key consideration | WCAG AAA mandatory. Trust paramount. |

## Tokens (Legal Services palette, #40)
| Role | Hex |
|---|---|
| Primary (authority navy) | #1E3A8A |
| On primary | #FFFFFF |
| Accent (trust gold) | #B45309 |
| Background | #F8FAFC |
| Foreground | #0F172A |
| Card | #FFFFFF |
| Muted surface | #E9EEF5 |
| Muted foreground | #475569 |
| Border | #CBD5E1 |
| Destructive / alert | #DC2626 |
| Focus ring | #1E3A8A |
Dark mode: background #0B1220, foreground #E2E8F0, card #111A2E, muted #1B2640, muted fg #94A3B8, border #26324A, primary #3563C9 (white text on it passes 4.5:1; a lighter blue failed at about 2:1), accent #F0A35E.

## Typography (pairing #16 "Corporate Trust")
Headings Lexend (pairing #16, enterprise, government, accessibility-focused). Body: Noto Sans instead of the pairing's
Source Sans 3, for two reasons found in testing: CSS rejects an unquoted family name ending in a digit (Streamlit's theme
font setting cannot quote it), and Noto covers Hindi and Malayalam for litigant notices. Base 16px, line-height 1.5, no text under 12px.

## Rules applied (skill priority table)
1. Accessibility (critical): contrast 4.5:1 minimum (AAA 7:1 for body text on this palette), visible focus, labels on every control.
2. Touch and interaction (critical): targets at least 44x44px, loading feedback on every long step.
5. Layout: no horizontal scroll; wide tables scroll in their own container.
6. Typography and color: semantic tokens only, no raw hex in components.
8. Forms and feedback: visible labels, errors next to the field they concern, helper text.
9. Navigation: one clear start page, predictable order, at most 5 top-level groups.
10. Charts: legends and direct labels, never color alone; status uses icon + word + color.

## Semantic status (never color alone)
Good: green #15803D with a check icon and a word. Warning: amber #B45309 with a word. Critical: red #DC2626 with a word.
