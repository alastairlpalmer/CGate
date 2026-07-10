# CGate — Full App & Codebase Review

**Date:** 2026-07-10 · **Branch reviewed:** `main` (commit `4d56db0`) · **Baseline:** all 134 tests pass.
**Method:** four parallel review passes — backend bug hunt, user-workflow audit, feature-gap analysis, and a mobile follow-up pass — each cross-checked against the prior `QA_REPORT.md`, `REMINDERS_QA_REPORT.md`, and `MOBILE_QA_REPORT.md` so nothing already fixed is re-reported. The two critical bugs and the top workflow defects were verified directly against the code; bugs 1–3 were reproduced empirically against a scratch database.

All paths are relative to `horse_management/` unless noted.

---

## Part 1 — Significant bugs requiring fixing

### 1. CRITICAL — Xero API push re-introduces the fractional-ownership overcharge that QA #1 fixed in the CSV path
**Where:** `xero_integration/services.py:99-109` (`build_xero_invoice_payload`).
The CSV export was fixed to emit `Quantity=1, UnitAmount=line_total` so Xero's computed line amount equals the owner's share. The API push path — whose docstring claims to "mirror" the CSV mapping — still sends `Quantity=item.quantity, UnitAmount=item.unit_price` (days × full daily rate).
**Reproduced:** horse owned 50/50, 30 days at £7/day → Alice's invoice is correctly £105.00, but the pushed Xero line is `30 × £7.00 = £210.00` (2× overcharge). Every "Push to Xero" for any fractional owner over-bills.
**Fix:** per line item emit `Quantity: '1'`, `UnitAmount: str(item.line_total)` (fall back to `quantity × unit_price` if `line_total` is None), exactly as `invoicing/utils.py:161-166` does.

### 2. CRITICAL — Split extra charges on horses with no OwnershipShare are silently never billed
**Where:** `invoicing/services.py:220-262` (`get_unbilled_charges`); `billing/models.py:113-117` (`split_by_ownership` defaults to `True`).
Direct charges bill via `charge.owner` (Case 1, requires `split_by_ownership=False`); split charges bill via `OwnershipShare` (Case 2). A split charge on a share-less horse matches neither case and stays `invoiced=False` forever — no invoice, no preview, no warning. Share-less horses are a supported state, and split charges on them are created routinely (the form checkbox defaults on; farrier/vet views and `bulk_health_apply` auto-create charges; `feed_out_create` passes `split_by_ownership=True` explicitly).
**Reproduced:** share-less horse, £150 June livery + £120 vet charge → invoice total £150.00; the £120 is silently dropped. This is silent revenue loss on exactly the horse population QA #2 was about.
**Fix:** in `get_unbilled_charges`, treat split charges on horses with no shares like direct charges (bill 100% to `charge.owner`); optionally normalise the flag in `ExtraChargeForm.clean`.

### 3. HIGH — Cancelling an invoice never releases its extra charges, so replacement invoices under-bill
**Where:** `invoicing/forms.py:56-62` (cancel transition); no code anywhere resets `invoiced`/`invoice` on cancellation.
`check_for_overlapping_invoices` excludes CANCELLED, so a replacement invoice for the same period is allowed and re-bills livery — but the cancelled invoice's ExtraCharges keep `invoiced=True`, are excluded from the replacement, and can never be billed again. They also drop out of the unbilled KPI, so nobody notices.
**Reproduced:** invoice £270 (£150 livery + £120 vet) → cancel → replacement invoice = £150.00.
**Fix:** on transition to CANCELLED, reset `invoiced=False, invoice=None` on linked charges (where not billed on another live invoice), and exclude cancelled invoices' line items in `_maybe_mark_split_charge_invoiced` and `ExtraCharge.unbilled_total`.

### 4. MEDIUM — Permission gap: non-staff "viewers" can end placements, deactivate horses, and create/edit billing charges via health endpoints
**Where:** `health/views.py:391-484` (`bulk_health_apply`, `@login_required` only) plus `FarrierCreateView`/`FarrierUpdateView`/`VetVisitCreateView`/`VetVisitUpdateView` (`LoginRequiredMixin` only).
`bulk_health_apply` with `action_type=actual_departure` sets `Placement.end_date` and flips `horse.is_active=False` — operations that are `staff_required` everywhere else. Ending a placement stops livery billing, so a read-only account can silently truncate revenue. The same endpoints also create/modify `ExtraCharge` amounts, which are staff-only in `billing/views.py`.
**Fix:** staff-gate the placement-mutating bulk actions and the charge-creating/updating side effects (or the whole endpoints, if viewers don't need health recording).

### 5. MEDIUM — Stored XSS in `quick_add_vet`
**Where:** `health/views.py:1017-1018`.
The user-supplied vet name is interpolated unescaped into an HTML fragment swapped into the DOM by HTMX, and persisted as a `ServiceProvider` for later pages. A name like `</option><img src=x onerror=…>` executes in any user's browser; any logged-in viewer can plant it.
**Fix:** use `django.utils.html.format_html` for the fragment.

### 6. MEDIUM — Costs page "unbilled" KPI re-introduces the QA #6 double-count
**Where:** `billing/views.py:297-308` (`CostsListView.get_context_data`).
QA #6 was fixed via `ExtraCharge.unbilled_total()` (used by dashboard and finances), but the Costs page computes `Sum('amount', filter=Q(invoiced=False))` — counting the full amount of split charges already partially invoiced (and, per bugs 2/3, permanently stranded ones).
**Fix:** use `ExtraCharge.unbilled_total()` here too.

### 7. MEDIUM — `yard_cost_duplicate` mutates data on GET
**Where:** `billing/views.py:366-383`; linked as a plain `<a href>` in `templates/billing/costs_list.html:188`.
Creates a new `YardCost` on any GET with no method check and no CSRF protection — link prefetching or a trivial `<img src=…>` CSRF creates duplicate cost records, inflating cost totals and the finances chart.
**Fix:** `@require_POST` and convert the link to a POST form/button (matching `feed_stock_clear`).

### 8. MEDIUM — Xero API payload misuses `owner.account_code` as the GL AccountCode
**Where:** `xero_integration/services.py:97`.
In the CSV path the owner's `account_code` is the invoice *Reference* and the ledger `*AccountCode` is fixed at `'200'`. The API path puts the owner's customer code into every line's `AccountCode`, so any owner with a code set (e.g. "SMITH01") posts revenue to a nonexistent/wrong GL account — Xero rejects or misfiles the invoice. (It already sets `Reference` separately at line 115, confirming the mix-up.)
**Fix:** hard-code `'200'` (or a settings value) for `AccountCode`.

### 9. LOW — Invoice generation is check-then-act; concurrent generation can duplicate invoices and double-bill charges
**Where:** `invoicing/services.py:303-327, 449-482`.
No DB-level guard on owner+period; two staff clicking "Generate monthly" simultaneously can both pass the overlap check. Invoice *numbers* are race-safe; overlap is not.
**Fix:** `select_for_update` on the owner row around check+create, or a Postgres exclusion constraint on `(owner, daterange(period_start, period_end))` excluding CANCELLED.

### 10. LOW — Bulk Xero CSV export includes DRAFT and CANCELLED invoices by default
**Where:** `invoicing/views.py:283-317`; per-invoice `invoice_csv` has no status guard either.
Importing the default export into Xero raises receivables for invoices voided locally. The API push path blocks cancelled invoices; the CSV paths don't.
**Fix:** exclude CANCELLED (and arguably DRAFT) by default; block/watermark cancelled invoices in `invoice_csv`.

### 11. LOW — `SERVE_MEDIA=True` serves receipts and photos with no authentication
**Where:** `horse_management/urls.py:36-46`.
`django.views.static.serve` is wired with no login check, so on Railway-style deployments every uploaded receipt image (predictable `receipts/%Y/%m/<filename>` paths) is world-readable.
**Fix:** wrap the serve view with `login_required`.

**Checked and clean:** settings (SECRET_KEY required, DEBUG off in prod, HSTS/secure cookies), notification task claim/rollback logic, Xero OAuth flow (state validated), invoice rounding reconciliation, placement overlap constraints, dependency pins.

---

## Part 2 — Non-optimised user workflows

Ranked by impact on daily use. The app already does a lot right (one-step "New Arrival", `?horse=` quick-add deep links, bulk health actions, HTMX-filtered lists, automated overdue promotion/reminders, duplicate-invoice guards) — these are the gaps that survive verification.

1. **Monthly billing is one-invoice-at-a-time after generation.** "Generate Monthly" creates N drafts in one click, but sending is per-row (`templates/invoicing/invoice_list.html:168-178`) and Mark-as-Paid exists *only* on the detail page. A 20-owner yard does 20 open/send round-trips monthly and one page-visit per invoice at reconciliation time. The bulk-selection pattern already exists (`templates/health/partials/bulk_action_bar.html`) — apply it to the invoice list with "Send selected" / "Mark selected paid".

2. **`InvoiceUpdateView` is unreachable from the UI.** `invoicing/urls.py` registers `<pk>/edit/` (status transitions incl. cancel, due date, notes) but no template links to it. Cancelling a wrong invoice or extending a due date requires hand-typing the URL — to a normal user these operations don't exist. Add Edit + explicit "Cancel Invoice" (with confirmation) to the invoice detail header. Note: fixing this makes bug #3 (stranded charges on cancel) *more* urgent.

3. **No payment recording** (see also Feature gap #1). `mark_as_paid` is binary, accepts no amount/date/method; a draft can't be marked paid, so a cash-paying owner with no email address has no UI path to "paid" at all (`invoice_send` errors without an email; mark-paid requires SENT/OVERDUE).

4. **Health-record creation ejects you from the horse's context.** Every health CreateView's `get_success_url` hard-codes the global health dashboard (`health/views.py:532, 642, 730, 783, 839, 891`), even when opened from a horse page via `?horse=`. Recording three things after a vet visit means re-finding the horse three times. Honour a `?next=` param (or redirect to `horse_detail` when `?horse=` was present) and add "Save & add another".

5. **Manual invoice creation's live preview never updates.** `invoice_create.html` has a preview panel and the backend has an HTMX `invoice_preview` endpoint (`invoicing/views.py:162-184`), but nothing wires them together — change owner or dates and the panel goes stale, so users submit blind. One-line fix: `hx-get`/`hx-trigger="change"` on the owner/date fields.

6. **Extra-charge form doesn't derive the owner from the selected horse.** Opened from the charges list, Horse and "Bill To (Owner)" are independent dropdowns (`templates/billing/charge_form.html:30-44`) with no linkage and no server-side consistency check — a mismatched pick mis-bills silently. Auto-select `horse.current_owner` on change and validate server-side.

7. **Bulk vet/farrier cost is charged in full to every selected horse with no hint.** `bulk_health_apply` (`health/views.py:434-473`) creates a charge of the entered cost *per horse* — a £280 yard visit across 8 horses bills £2,240. Label the field "Cost per horse" and/or offer "split total across selected" (the even-split logic already exists in `feed_out_create`).

8. **Dashboard widgets show problems but offer no actions.** "Vaccinations Due"/"Farrier Due" partials link only to the horse; "Outstanding Invoices" has no send-reminder or mark-paid controls. The Health dashboard already builds direct `?horse=` action links — mirror that pattern on the dashboard widgets.

9. **Invoice list: no date/period filter, and CSV export ignores the search box.** The export view accepts `date_from`/`date_to` but the UI never offers them; exporting while searching quietly exports more than what's on screen. Add a period filter, a filtered-totals row, and make export honour `search`.

10. **Minor redirect/prefill nits:** after "Add Horse" you land on the list, not the new horse's page where "Log Arrival" lives; `NewArrivalForm` supports only a single 100% owner (co-owned arrivals need a follow-up trip); dashboard Quick Find only matches active horses so departed horses are unfindable from it; the vaccination "View all →" goes to a standalone list page that duplicates the tabbed Health dashboard UI.

---

## Part 3 — Feature gaps and improvements

Ranked by business value (effort: S/M/L). What already exists was verified in code first — invoice emailing with PDF, worming + egg counts, vet visits, medical conditions, feed stock tracking, occupancy charting, and fractional-ownership splitting are all present and are *not* gaps.

1. **Payment recording / partial payments / credit notes (L — highest value).** No Payment model anywhere; an invoice is binary paid/unpaid. Real yards get part-payments, overpayments carried as credit, and arrival deposits; the "outstanding" KPI is wrong the moment anyone pays half. A `Payment` model (date, amount, method, invoice FK) + owner balances is the biggest step toward being the yard's system of record.
2. **Automated Xero sync (S/M).** Push and status-check exist but are click-per-invoice. A "push all unsynced" bulk action plus a nightly Celery task calling `check_xero_invoice_status` closes the loop: invoices marked paid automatically, overdue reminders stop chasing people who already paid. (Blocked on bugs #1/#8 above being fixed first.)
3. **VAT handling (M) — currently an active inconsistency, not just a gap.** `Invoice.recalculate_totals` hard-codes no tax and the PDF prints `VAT: £0.00`, yet the Xero push sends `TaxType: OUTPUT2` with `LineAmountTypes: Exclusive` when a VAT registration is set — Xero would add 20% on top of what the PDF told the owner. Add a VAT rate to settings/line items and compute totals once.
4. **Aged debtors & owner statements (M).** Only a single outstanding total exists. With payments (#1), a 30/60/90 aging view and a printable/emailable per-owner statement is what the bookkeeper actually chases people with.
5. **Documents/attachments (M).** Horse has `passport_number`/`has_passport` fields but nowhere to store the passport scan, insurance cert, or loan agreement. A generic `Document(horse/owner FK, file, type, expiry_date)` with expiry reminders (reusing the reminder-task pattern) covers passports, insurance, and similar paperwork in one model.
6. **Auto-generation of monthly invoices (S).** Generation is duplicate-safe and one click but someone must remember. A beat task on the 1st creating drafts (optionally emailing after a review window) removes the most important recurring chore — all the pieces exist.
7. **Calendar view + iCal feed (M).** Due dates exist everywhere (vaccination/farrier/vet follow-up/expected departure/EHV/foaling) but only as dashboard lists. A month grid plus an iCal subscription lets staff plan vet/farrier days and see foaling clusters.
8. **Audit trail (S/M).** No history package anywhere; invoice amounts, rates, and ownership shares are silently mutable, and charges/costs have hard-delete views. `django-simple-history` on Invoice, ExtraCharge, Placement, OwnershipShare is cheap insurance for owner disputes.
9. **Weight / body-condition scoring (S).** Nothing exists; core welfare tracking for a grass-livery/breeding operation (especially laminitis-prone ponies already flagged in MedicalCondition). Simple dated-record model + sparkline on horse detail.
10. **Dentist/physio/medication as first-class records (S).** These exist only as charge types — you can bill them but not schedule them. A `DentistVisit` with `next_due_date` slots into the existing health/reminder patterns; ongoing medication (dose, frequency, end date) linked to MedicalCondition is similarly small.
11. **Owner portal (L).** Owners are data rows, not users. A read-only portal (my horses, health records, invoices + the existing `card_payment_url`) reduces phone-tag, but it's a large surface — do after payments/statements exist.
12. **Generic data export & backup story (S).** Only invoices export. Add per-list CSV export (horses, owners, placements, health) and document/automate DB + media backups (CUTOVER.md covers deploys, not backups).
13. **REST API for mobile (M/L — lowest priority).** README mentions it but no DRF exists. The responsive site + PWA route (Part 4) covers field use; only invest here if offline-first native becomes a real need.

---

## Part 4 — Mobile improvements

Cross-checked against `MOBILE_QA_REPORT.md` and the merged Mobile Batches 1b/2/3/4/5.

### Outstanding from the prior report
- **O1 (HIGH) — "tables → cards" only reached 3 views; 14 templates still ship raw multi-column tables at 390px:** `placement_list.html` (7 columns, plus hand-rolled pagination), all seven standalone health list pages (directly linked from dashboard "View all →"), the dashboard's own "Outstanding Invoices" table, and the tables on owner/location detail and the feed-store ledger. The `.data-table-cards` CSS already exists — this is pure template work.
- **O2 (LOW-MED) — row-link hit areas are now largely fixed** (the report annotation is stale; `.list-card-link` covers the main lists). Residuals: dashboard list partials (only the horse-name text is tappable) and the O1 tables.
- **O3 (MED) — the Manage Ownership page missed the Batch 1b fix:** `templates/horses/horse_ownership.html:62-78` still has small Primary/DELETE checkbox targets; copy the exact `horse_form.html` markup across (destructive control).
- **O4/O5/O6 (LOW):** bulk-select checkbox labels are 40px (bump to 44), health tab-strip has no scroll affordance (CSS mask fade), paired date inputs use unconditional `grid-cols-2` (cram at ≤360px).
- **Closed since the report:** aria-labels on icon buttons, form-footer offsets, login page fixes — all verified done.

### New findings
- **N1 (HIGH) — zero PWA readiness, not even a favicon:** no manifest, no apple-touch-icon, no theme-color. For an app used at a yard on phones, "Add to Home Screen" yields a generic letter icon in browser chrome. An afternoon's work: manifest (`display: standalone`, 192/512 + maskable icons, brand colors), favicon, theme-color meta.
- **N2 (HIGH perf) — Chart.js (209 KB) loads on every page but is used on two** (`base.html:32`; only finances and location detail draw charts). Lazy-load it when a `canvas[data-chart]` is present (keeping the `hx-boost`-surviving loader in base).
- **N3 (HIGH perf) — no image thumbnails:** 32px avatars render the original photo URL (capped at 2560px/q90) — a 30-horse list can pull tens of MB over yard 4G. Generate a small rendition at save time (160px avatar + 480px hero) and add width/height attributes.
- **N4/N5/N6 (MED):** horse-detail Timeline header crams four buttons into a non-wrapping row at 390px (the Quick Actions grid below already covers them — hide or collapse on mobile); timeline filter pills / placement tabs / health tabs are ~30-41px tall (add the established `min-h-[44px] sm:min-h-0` idiom); the inline "Depart" confirm row can clip its Confirm button at ≤360px.
- **N7-N10 (LOW):** missing `inputmode="numeric"` on integer fields (payment terms, capacity, age, intervals, egg counts — money fields are already exemplary); the bulk-modal close X is ~20px; no landscape safe-area insets on the tab bar; fonts load 3 families × 9 weights from Google CDN (self-host the 4-5 used woff2 files via the already-configured whitenoise).

### Strategic mobile improvements
1. **Global quick-add "+" in the tab bar** opening a bottom sheet (Farrier / Vaccination / Worming / Egg count / Vet visit / Move / New arrival) then a horse picker — all create views already accept `?horse=` deep links, so this is mostly UI glue. This is the "yard worker with dirty gloves" feature.
2. **Installable PWA with an offline write queue** — after N1, a service worker with app-shell precache and an IndexedDB replay queue for health-record POSTs, so recording a farrier visit in a signal-dead stable block never loses data.
3. **Web push for the existing reminder engine** — "Dobbin's flu jab is overdue" deep-linking to `vaccination_create?horse=` closes the loop for staff who never open a laptop.
4. **Manifest shortcuts, per-horse QR door tags, camera-first photo capture (`accept="image/*"`), location-scoped "yard round" bulk entry, and sticky per-user form defaults** (last-used farrier/wormer/vet).

---

## Suggested priority order

| Priority | Items | Why |
|---|---|---|
| 1 — Fix now | Bugs 1, 2, 3 (+ 8, since it's the same function as 1) | Money-wrong: active over- and under-billing paths, two of them regressions of previously fixed issues |
| 2 — Fix soon | Bugs 4, 5, 6, 7; Workflow 2 (expose invoice edit/cancel, paired with bug 3's fix) | Security/permission gaps and wrong KPIs |
| 3 — High-value quick wins | Workflows 1, 4, 5, 7; Feature 6 (auto monthly drafts); Mobile O1, O3, N1, N2 | Biggest daily-use friction, mostly S-effort |
| 4 — Foundational features | Features 1 (payments), 2 (auto Xero sync), 3 (VAT), 4 (statements) | Turns the app into the financial system of record |
| 5 — Strategic | Feature 5, 7, 8; Mobile strategic 1-3 (quick-add sheet, PWA offline, push) | Compounding value once the above land |
