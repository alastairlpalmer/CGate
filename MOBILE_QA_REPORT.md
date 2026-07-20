# Yardway (formerly CGate) — Mobile Experience QA Report

> **✅ 2026-07-20 follow-up pass (Yardway rebrand + tap-target close-out).**
> The app was renamed **Yardway** across every user-facing surface (page titles, headers, login/auth pages, PWA manifest & home-screen name, footer, Xero copy, README, default from-address). A fresh end-to-end mobile sweep (390×844, all core workflows driven: new arrival, move, vaccination, charge, invoice create/payment, feed, departure, More-sheet nav) confirmed all workflows complete correctly; the full Django suite (413 tests) passes.
> Fixes shipped in this pass: sub-44px tap targets cut **94 → 51** (remainder are stretched-card links, ≥44px-tall short words, or checkboxes with 44px label hit-areas — i.e. no real offenders left): a shared `.tap-pad` utility now pads inline text links (row names, "View all →", back links, settings Edit, mailto/tel) to ≥44px hit boxes on touch layouts with zero layout shift; unclassed checkboxes (ownership **DELETE** was still 13px — T1.1 residual) are globally 20px; the `.form-toggle` switch gained a 44px overlay hit-area; location-detail tabs use new `tab-underline` components (44px tall, scrollable with edge fade — closes T3.3 for this screen); Costs period pills are 44px on mobile; the location-detail stat block is a 3-across row (T3.1 pattern); file inputs are 16px on mobile (no iOS zoom); the "+ add vet" button is ≥44px wide with a dynamic aria-label (T3.2/T4.2 close-out).

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
> **✅ Batch 1 (largely done, PR):** `.btn`, `.form-input`, `.form-select` now carry `min-h-[44px] sm:min-h-0` (44 px on mobile, natural height on desktop) and `.form-checkbox` went 13 → 20 px — via `static/css/input.css` (rebuilt `styles.css`). Row-action icons already use `.btn-icon` (44 px on mobile). Measured **sub-44 tap targets fell 228 → 104**; desktop verified unchanged. **Batch 1b** then took the horse-list **tab pills** to 44 px and gave the ownership **Primary/Remove** checkboxes 44 px label hit-areas (see T2.3, T3.1). Still open (low priority): making every primary list-row *name* link fill its row (T3.2) and enlarging bulk-select checkbox hit-areas — noting ~half of the residual 104 were secondary `mailto:`/`tel:` links that need not be 44 px.

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
> **✅ Fixed in Batch 2 (PR):** `.form-footer` offset its bottom by `4rem + env(safe-area-inset-bottom)`, but the tab bar is `4rem + max(env(safe-area-inset-bottom), 8px)` tall — so whenever the safe-area inset was under 8px (emulators / most non-notch contexts) the footer overlapped the nav by ~8px, with zero gap even on notched devices. Now offset by `4rem + max(env(safe-area-inset-bottom, 0px), 8px) + 8px` (`static/css/input.css`, rebuilt). Verified: footer bottom now sits **7px above** the nav top (was ~9px overlap) across horse/placement/charge forms; the fix is in the shared class so it applies to every form uniformly (see T3.4).

**Where:** `.form-footer` (sticky, 63 px, used by `horse_form.html`, `horse_new_arrival.html`, `horse_move.html`, `horse_arrive.html`, `horse_ownership.html`, `placement_form`, `location_form.html`, `location_arrive.html`, `invoice_form.html`, `vaccination`/`charge` forms, …) + the fixed bottom nav in `base.html:214` (`fixed bottom-0 … 73px`).
**Impact:** On phones the two bars stack — the sticky footer's bottom edge (≈780 px) sits **on top of** the nav's top edge (≈771 px). The primary action ("Save Changes", "Create & Arrive", "Add Placement", …) is jammed against the nav's tap zone, inviting a mis-tap that navigates away mid-form, and the two bars eat ~136 px (16%) of an 844 px viewport. `invoice_create.html` conspicuously does **not** use the sticky footer (buttons are inline), so the behaviour is also inconsistent.
**Evidence:** fixed/sticky-bar scan (`horse_edit`, `new_arrival`, `placement_add`, `vax_add`, `charge_add` all show both bars); `scratchpad/mob/c_horse_edit.png`.
**Fix (dev-ready):** on mobile, offset the sticky footer above the nav (`bottom: 73px` / `mb-[73px]`, or `bottom: calc(73px + env(safe-area-inset-bottom))`), **or** hide the bottom nav while a form is focused/open, **or** drop the sticky footer on mobile and let the actions scroll inline (matching `invoice_create`). Pick one and apply consistently to the shared `.form-footer`.
**Acceptance:** on a 390×844 viewport, the form's primary button and the bottom-nav items never overlap; ≥ 8 px gap between them.
**Effort:** S–M (single shared component; decide the pattern).

---

## T2 — High

### T2.1 — Login inputs are 13 px → iOS auto-zooms on focus
> **✅ Fixed in Batch 3 (PR) — and it turned out to be bigger than the font.** Root cause of the 13 px/21 px measurement: the auth pages loaded **Tailwind from the Play CDN** (`cdn.tailwindcss.com`) via `_auth_head.html`, not the compiled `styles.css` — so when that CDN is blocked/slow (as in this sandbox, and on many networks) the **entire login page rendered unstyled** (raw browser-default inputs). Fix: `_auth_head.html` now loads the compiled `styles.css` + vendored Alpine (no CDN; classes are already in the Tailwind content glob) with non-render-blocking fonts, and the auth inputs/buttons were bumped to `text-base` (16 px) + `min-h-[44px]`. Verified with the CDN blocked: the page renders fully styled, inputs are **16 px / 44 px**, submit button 44 px, and login still authenticates (302 → `/`). Applied to login + password-reset/confirm/change templates.

**Where:** `templates/registration/login.html` (raw Django auth widgets, not the app's `.form-input`). Measured username/password at **13 px** font, input height **~21 px**.
**Impact:** iOS Safari auto-zooms the page when a focused input's font is < 16 px, then leaves it zoomed — a jarring first impression on the very first screen every user sees, and the 21 px input height is also a poor tap target.
**Fix:** apply the app input styling to the auth fields (`.form-input`, which is full-width and taller) and guarantee **≥ 16 px** font on inputs (`text-base` on mobile). Do the same for the password-reset/change templates for consistency.
**Acceptance:** focusing the login fields on iOS Safari does not trigger zoom; inputs ≥ 44 px tall, ≥ 16 px font.
**Effort:** S.

### T2.2 — Key data tables are cramped / clipped on narrow screens
> **✅ Fixed in Batch 4 (PR):** added a reusable `.data-table-cards` modifier (`static/css/input.css`) that, below `sm`, stacks each row into a labelled card (label from `data-label`, value right-aligned; action/empty cells stay plain blocks). Applied to the Health **Action Required** & **Coming Up** tables and the 8-column **Costs** table (with `data-label`s per cell). Verified on mobile: the overdue status and due date are now fully readable ("181 days overdue / 01 Jan 2026", previously clipped to "overc…/01 Jar…"), long cost descriptions/horse names wrap, no horizontal scroll. The helper can be dropped onto other list tables the same way (Batch 1b/5).

**Where:** Health "Action Required" & "Coming Up" tables (`health` dashboard) and the Costs table (`/billing/costs/`). At 390 px the right-most column is squeezed: the health **DUE** column wraps to "181 days overc…" and the date to "01 Jar…" (both clipped); the Costs table's `SUPPLIER` header and the empty-state text are cut off at the right edge inside their scroll container.
**Impact:** The single most important datum in the health view — *how overdue* something is and *when* it's due — is truncated. Users can't read it without horizontal scrolling they may not realise is available.
**Evidence:** `scratchpad/mob/f_health.png`, `scratchpad/mob/c_costs.png`.
**Fix:** for these record tables, switch to a stacked **card layout** below `sm:` (label/value pairs per row) — the pattern already used successfully on the invoice detail — instead of a multi-column table. If a table is kept, give the scroll container a visible affordance (edge fade / "scroll →" hint) and make sure the last column isn't clipped.
**Acceptance:** overdue status and due date are fully readable at 390 px with no clipping; no reliance on non-obvious horizontal scroll for primary data.
**Effort:** M (per-view template work; a shared "responsive table → cards" partial would cover several screens).

### T2.3 — Ownership & bulk-select checkboxes are 13 px (destructive actions included)
> **✅ Addressed (Batch 1 + 1b):** all `.form-checkbox` controls are 20 px (was 13), and the ownership formset's **Primary** and destructive **Remove/DELETE** controls are now wrapped in `min-h-[44px]` labels so the whole label is a 44 px tap target (the DELETE box itself stays small but its label click-area is full-height). Remaining: bulk-select checkboxes in list tables still rely on the 20 px box — low priority.

**Where:** ownership formset (`has_passport`, `is_active`, `is_primary_contact`, row **DELETE**) and Horses-list bulk-select boxes. (Same root cause as T1.1 but called out separately because it gates data-entry and a *destructive* delete.)
**Impact:** the 13 px DELETE checkbox next to a small trash icon is an easy mis-tap with irreversible intent; the "Primary" contact toggle drives billing.
**Fix:** covered by the T1.1 checkbox sizing + 44 px label wrapper; additionally give the ownership row DELETE a clearer, larger control (e.g. a labelled "Remove" `.btn-icon-danger`).
**Acceptance:** all form checkboxes ≥ 44 px tap area; delete affordance is unambiguous.
**Effort:** S (with T1.1).

---

## T3 — Medium

### T3.1 — KPI card layout is inconsistent and scroll-heavy
> **✅ Fixed in Batch 1b/5 (PR):** Dashboard, Finances and Costs KPI rows changed from `grid-cols-1 sm:grid-cols-2` to `grid-cols-2 lg:grid-cols-4`, matching Health — a **2×2 grid on phones**, halving the scroll (e.g. the dashboard's "Recent Activity" is now above the fold). Verified, no overflow.

**Where:** Dashboard, Finances, and Costs stack their 4 KPI cards **one per row** (full-width), forcing long scrolls before the actual content; Health uses a tidy **2×2 grid**.
**Fix:** standardise KPI blocks to `grid grid-cols-2 gap-3` on mobile (single column only below ~340 px if needed). Halves the vertical scroll and unifies the look.
**Effort:** S.

### T3.2 — Small tap height on list text-links
> **Partially addressed / still open.** The horse-list segmented **tab pills** (Active/Departed, group-by) are now 44 px (Batch 1b). Making every primary list-row *name* link fill its row is still open — it's per-list template work and many flagged links are secondary `mailto:`/`tel:` links that shouldn't be 44 px anyway. Lower priority; deferred.

**Where:** horse/owner name links and "View all →" in dashboard lists, invoice numbers in the invoice list, location links in grouped lists — all ~16–24 px tall.
**Fix:** make the whole list row/cell the tap target (`block py-3` / stretch a link over the row) rather than just the text glyph height.
**Effort:** S–M.

### T3.3 — Health tab strip scrolls horizontally with no affordance
> **Deferred (low value).** A right-edge fade needs JS to only show when actually scrollable; the tabs are usable (42–44 px, horizontally scrollable). Left for a later pass.

**Where:** Health page tab bar (Overview / Vaccinations / Farrier / Worming / Egg Counts / Conditions / Vet Visits) overflows; the 7th tab is cut mid-word with no indication more exist.
**Fix:** add a scroll affordance (right-edge fade, or `scroll-snap` with a partial next-tab peek), or collapse overflow tabs into a "More ▾".
**Effort:** S.

### T3.4 — Inconsistent form-action pattern
> **✅ Resolved with T1.2.** Correction to the original observation: `invoice_create.html` **does** use the shared `.form-footer` — it simply doesn't *stick* to the bottom because the live-preview card follows it in the flow (so the earlier runtime scan didn't detect a bottom bar there). All forms use the same class, so the Batch 2 fix applies uniformly; no separate work needed.

---

## T4 — Low / polish

- **T4.1 — Truncated input placeholders.** ✅ *Fixed in Batch 1b/5* — shortened to "Search horses…" / "Search invoices…".
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
