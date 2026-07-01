# CGate — Mobile Experience QA Report

**Focus:** Mobile usability of the core user workflows (not billing correctness — see `QA_REPORT.md` for that).
**Date:** 2026-07-01 · **Build:** `main` (all `QA_REPORT.md` fixes merged).
**Method:** Real mobile emulation via Playwright — iPhone-class viewport **390 × 844**, `device_scale_factor` 3, touch enabled, iOS Safari UA. Drove 26 pages logged in as an admin; programmatically measured horizontal overflow, tap-target sizes, input font sizes, and fixed/sticky bars; captured full-page + above-the-fold screenshots. The Django Debug Toolbar (DEBUG=True in dev) was excluded from all measurements and hidden in screenshots — it does not ship in production.

**Devices in scope:** modern iOS/Android phones (360–430 px logical width). Smallest common (iPhone SE / 320 px) called out where relevant.

---

## Verified good (no action needed)

These held up well and are worth protecting against regression:

- **No horizontal page overflow** on any of the 26 pages (`scrollWidth == viewport`). Wide tables are wrapped in scroll containers rather than blowing out the layout.
- **Viewport meta** is correct: `width=device-width, initial-scale=1.0, viewport-fit=cover` (note the `viewport-fit=cover` + `tab-bar-safe` class = safe-area aware).
- **Bottom-nav clearance:** `#main-content` carries `padding-bottom: 112px` vs the 73 px fixed nav, so page content is never trapped behind the nav.
- **Invoice detail** (`/invoicing/<pk>/`) is an exemplary mobile layout — a 2-column item/amount list with wrapping descriptions, a prominent "Amount Due" card, and correct totals even for a 12-line invoice with very long horse names.
- **Invoice create** form + **live preview** stack cleanly; the two date inputs sit side-by-side at 390 px.
- **Charts** (Finances: revenue-vs-cost line, site-capacity bars) are responsive and legible on mobile.
- **List cards** (Horses grouped by location) wrap long names and truncate long owner names gracefully.
- `.btn-icon` is already defined `w-11 h-11 sm:w-8 sm:h-8` (44 px on mobile) and buttons use `touch-manipulation` — good foundations already in place.

---

## Severity tiers

| Tier | Meaning |
|------|---------|
| **T1 — Critical** | Directly impairs a core task on a phone for essentially every user; fix first. |
| **T2 — High** | Significant friction / accessibility failure on common flows. |
| **T3 — Medium** | Consistency / efficiency / polish that a mobile user will feel. |
| **T4 — Low** | Minor polish and edge devices. |

---

## T1 — Critical

### T1.1 — Interactive controls fall below the 44 px minimum touch target
> **✅ Batch 1 (largely done, PR):** `.btn`, `.form-input`, `.form-select` now carry `min-h-[44px] sm:min-h-0` (44 px on mobile, natural height on desktop) and `.form-checkbox` went 13 → 20 px — via `static/css/input.css` (rebuilt `styles.css`). Row-action icons already use `.btn-icon` (44 px on mobile). Measured **sub-44 tap targets fell 228 → 104**; desktop verified unchanged. **Remaining (Batch 1b, per-template):** full 44 px *hit areas* for checkboxes (incl. the formset DELETE, which has no `.form-checkbox` class), the ad-hoc segmented **tab pills** (~32 px inline classes — candidate for a shared `.seg-tab`), and primary list-row links (T3.2). ~Half of the residual 104 are secondary `mailto:`/`tel:` links that need not be 44 px.

**Where:** `static/css/input.css` — `.btn` (`@apply … px-4 py-2 text-sm …`) renders **~36–40 px tall**; `.form-checkbox` has no size (browser default **~13 px**). Confirmed on measurement across nearly every page (buttons: "Create Invoice" 144×**36**, "Download PDF" 150×**38**, "Add Horse" 100×**38**, form-footer Save/Cancel ~**40**; checkboxes: `has_passport`, `is_active`, `ownership_shares-*-is_primary`, `ownership_shares-*-DELETE`, and the bulk-select boxes all **13×13**).
**Impact:** Apple HIG and Google both specify a 44 px (≈48 dp) minimum. Sub-40 px buttons and 13 px checkboxes are hard to hit accurately, especially the delete/primary checkboxes on the ownership formset and the row bulk-select boxes — the highest-consequence taps in the app.
**Evidence:** `scratchpad/mob/c_horse_edit.png` (checkboxes), tap-target scan (every page reports 3–21 sub-44 controls after excluding the toolbar).
**Fix (dev-ready):**
- Add a min touch height to the base button: `.btn { @apply … min-h-[44px]; }` (keep `text-sm`; the extra height is invisible on desktop and correct on mobile).
- Size checkboxes/radios: `.form-checkbox { @apply h-5 w-5 …; }` and wrap each in a `label` with `min-h-[44px] inline-flex items-center gap-2` so the whole label is the tap target.
- Ensure **all** row action icons (Horses list edit/move, "Book", table row actions) use `.btn-icon` (already 44 px on mobile) rather than bare `<a><svg>` — the scan still found 16×16 and 24 px action controls that bypass it.
**Acceptance:** every visible `a/button/input/select` on the audited pages measures ≥ 44 px in both dimensions at 390 px width (re-run `mobile_qa2.py`; `smallTap == 0` excluding intentional inline text links).
**Effort:** M (mostly a handful of shared component classes; verify the row-action templates).

### T1.2 — Sticky form footer collides with the fixed bottom nav on every create/edit form
**Where:** `.form-footer` (sticky, 63 px, used by `horse_form.html`, `horse_new_arrival.html`, `horse_move.html`, `horse_arrive.html`, `horse_ownership.html`, `placement_form`, `location_form.html`, `location_arrive.html`, `invoice_form.html`, `vaccination`/`charge` forms, …) + the fixed bottom nav in `base.html:214` (`fixed bottom-0 … 73px`).
**Impact:** On phones the two bars stack — the sticky footer's bottom edge (≈780 px) sits **on top of** the nav's top edge (≈771 px). The primary action ("Save Changes", "Create & Arrive", "Add Placement", …) is jammed against the nav's tap zone, inviting a mis-tap that navigates away mid-form, and the two bars eat ~136 px (16%) of an 844 px viewport. `invoice_create.html` conspicuously does **not** use the sticky footer (buttons are inline), so the behaviour is also inconsistent.
**Evidence:** fixed/sticky-bar scan (`horse_edit`, `new_arrival`, `placement_add`, `vax_add`, `charge_add` all show both bars); `scratchpad/mob/c_horse_edit.png`.
**Fix (dev-ready):** on mobile, offset the sticky footer above the nav (`bottom: 73px` / `mb-[73px]`, or `bottom: calc(73px + env(safe-area-inset-bottom))`), **or** hide the bottom nav while a form is focused/open, **or** drop the sticky footer on mobile and let the actions scroll inline (matching `invoice_create`). Pick one and apply consistently to the shared `.form-footer`.
**Acceptance:** on a 390×844 viewport, the form's primary button and the bottom-nav items never overlap; ≥ 8 px gap between them.
**Effort:** S–M (single shared component; decide the pattern).

---

## T2 — High

### T2.1 — Login inputs are 13 px → iOS auto-zooms on focus
**Where:** `templates/registration/login.html` (raw Django auth widgets, not the app's `.form-input`). Measured username/password at **13 px** font, input height **~21 px**.
**Impact:** iOS Safari auto-zooms the page when a focused input's font is < 16 px, then leaves it zoomed — a jarring first impression on the very first screen every user sees, and the 21 px input height is also a poor tap target.
**Fix:** apply the app input styling to the auth fields (`.form-input`, which is full-width and taller) and guarantee **≥ 16 px** font on inputs (`text-base` on mobile). Do the same for the password-reset/change templates for consistency.
**Acceptance:** focusing the login fields on iOS Safari does not trigger zoom; inputs ≥ 44 px tall, ≥ 16 px font.
**Effort:** S.

### T2.2 — Key data tables are cramped / clipped on narrow screens
**Where:** Health "Action Required" & "Coming Up" tables (`health` dashboard) and the Costs table (`/billing/costs/`). At 390 px the right-most column is squeezed: the health **DUE** column wraps to "181 days overc…" and the date to "01 Jar…" (both clipped); the Costs table's `SUPPLIER` header and the empty-state text are cut off at the right edge inside their scroll container.
**Impact:** The single most important datum in the health view — *how overdue* something is and *when* it's due — is truncated. Users can't read it without horizontal scrolling they may not realise is available.
**Evidence:** `scratchpad/mob/f_health.png`, `scratchpad/mob/c_costs.png`.
**Fix:** for these record tables, switch to a stacked **card layout** below `sm:` (label/value pairs per row) — the pattern already used successfully on the invoice detail — instead of a multi-column table. If a table is kept, give the scroll container a visible affordance (edge fade / "scroll →" hint) and make sure the last column isn't clipped.
**Acceptance:** overdue status and due date are fully readable at 390 px with no clipping; no reliance on non-obvious horizontal scroll for primary data.
**Effort:** M (per-view template work; a shared "responsive table → cards" partial would cover several screens).

### T2.3 — Ownership & bulk-select checkboxes are 13 px (destructive actions included)
> **◑ Partially addressed in Batch 1:** all `.form-checkbox` controls are now 20 px (was 13). Remaining for Batch 1b: 44 px label hit-areas, and the formset **DELETE** box still renders at 13 px (needs the `.form-checkbox` class / a labelled remove control).

**Where:** ownership formset (`has_passport`, `is_active`, `is_primary_contact`, row **DELETE**) and Horses-list bulk-select boxes. (Same root cause as T1.1 but called out separately because it gates data-entry and a *destructive* delete.)
**Impact:** the 13 px DELETE checkbox next to a small trash icon is an easy mis-tap with irreversible intent; the "Primary" contact toggle drives billing.
**Fix:** covered by the T1.1 checkbox sizing + 44 px label wrapper; additionally give the ownership row DELETE a clearer, larger control (e.g. a labelled "Remove" `.btn-icon-danger`).
**Acceptance:** all form checkboxes ≥ 44 px tap area; delete affordance is unambiguous.
**Effort:** S (with T1.1).

---

## T3 — Medium

### T3.1 — KPI card layout is inconsistent and scroll-heavy
**Where:** Dashboard, Finances, and Costs stack their 4 KPI cards **one per row** (full-width), forcing long scrolls before the actual content; Health uses a tidy **2×2 grid**.
**Fix:** standardise KPI blocks to `grid grid-cols-2 gap-3` on mobile (single column only below ~340 px if needed). Halves the vertical scroll and unifies the look.
**Effort:** S.

### T3.2 — Small tap height on list text-links
> **Deferred to Batch 1b:** needs per-list template work (make the primary name link fill the row); left out of Batch 1 to keep it a low-risk CSS-only change.

**Where:** horse/owner name links and "View all →" in dashboard lists, invoice numbers in the invoice list, location links in grouped lists — all ~16–24 px tall.
**Fix:** make the whole list row/cell the tap target (`block py-3` / stretch a link over the row) rather than just the text glyph height.
**Effort:** S–M.

### T3.3 — Health tab strip scrolls horizontally with no affordance
**Where:** Health page tab bar (Overview / Vaccinations / Farrier / Worming / Egg Counts / Conditions / Vet Visits) overflows; the 7th tab is cut mid-word with no indication more exist.
**Fix:** add a scroll affordance (right-edge fade, or `scroll-snap` with a partial next-tab peek), or collapse overflow tabs into a "More ▾".
**Effort:** S.

### T3.4 — Inconsistent form-action pattern
**Where:** most forms use the sticky `.form-footer`; `invoice_create.html` uses inline buttons.
**Fix:** whichever resolution is chosen for T1.2, apply it uniformly so every form behaves the same.
**Effort:** S (folds into T1.2).

---

## T4 — Low / polish

- **T4.1 — Truncated input placeholders.** "Search by name, owner,…" / "Search by invoice # or owner…" cut off. Shorten placeholders for narrow widths.
- **T4.2 — Icon-only actions lack accessible labels.** Row edit/move/book icons are icon-only; add `aria-label`/`title` for screen readers and long-press tooltips.
- **T4.3 — Paired date inputs on ≤320 px.** The side-by-side Period Start/End inputs are comfortable at 390 px but will tighten on iPhone SE; consider stacking below ~360 px.
- **T4.4 — Native date-input locale.** Under the en-US test UA the date control shows `mm/dd/yyyy`; the app is `en-gb`. The native control follows the *device* locale, so a UK user's phone shows `dd/mm` — verify on a real UK device; likely a non-issue, noted for completeness.

---

## Suggested fix batches (for planning)

1. **Touch-target pass (T1.1, T2.3, T3.2)** — one focused PR on `input.css` (`.btn` min-height, `.form-checkbox` sizing, label wrappers) + route stray row actions through `.btn-icon`. Biggest UX win per unit effort; low regression risk (additive sizing).
2. **Form footer vs bottom nav (T1.2, T3.4)** — decide the pattern, apply to shared `.form-footer`.
3. **Login/auth inputs (T2.1)** — quick, high-visibility.
4. **Responsive tables → cards (T2.2)** — build one shared partial, apply to Health + Costs (and reuse elsewhere).
5. **Layout consistency (T3.1, T3.3, T4.x)** — KPI grid, tab affordance, polish.

Batches 1–3 are small and independently shippable; I can implement any of them on request (with before/after mobile screenshots and the `mobile_qa2.py` tap-target scan as the regression check).

---

## Reproduction

- `mobile_seed.py` adds mobile-stress data (long owner/horse names, a 12-line invoice) on top of `seed_qa.py`.
- `mobile_qa.py` — full audit (metrics + screenshots), includes the Debug Toolbar.
- `mobile_qa2.py` — clean metrics (toolbar excluded): overflow, tap targets, input fonts, fixed-bar detection. Use as the regression gate.
- `mobile_shots.py` — clean screenshots (toolbar hidden), full-page + above-the-fold, under iPhone-class emulation.
- Screenshots: `scratchpad/mob/c_*.png` (full page), `f_*.png` (above the fold).
