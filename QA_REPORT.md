# CGate QA Report

**App:** CGate — Django horse-livery management (horses, owners, locations, placements, monthly invoicing, PDF/Xero export, health tracking, extra charges, email reminders)
**Tested:** 2026-07-01 · local SQLite dev DB · Python 3.11.15 / Linux · `EMAIL_BACKEND=console` (no real mail sent) · commit on branch `claude/cgate-qa-testing-fn32k8`
**Method:** Read the invoicing/ownership logic, seeded realistic data with a hand-computed ground-truth table, verified invoice math server-side via `InvoiceService`, generated PDFs, exercised the UI with Playwright (auth gating, role access control, responsive layout at 375/768/1280px, console errors).

> **Note on stack vs. brief:** WeasyPrint is **not** installed (not in `requirements.txt`), so PDF generation always uses the ReportLab fallback. Tailwind is **compiled** to `static/css/styles.css` (not a runtime CDN); Alpine/HTMX/Chart.js are vendored locally. The only external runtime dependency is Google Fonts.

---

## Summary (severity-sorted)

| # | Severity | Title | Area |
|---|----------|-------|------|
| 1 | **Critical** | Xero CSV export overcharges fractional-ownership livery lines (exports full rate × days, not the owner's share) | Xero export |
| 2 | **Critical** | Horses placed without an `OwnershipShare` are silently never billed for livery | Placement / invoicing |
| 3 | **Critical** | Moving a horse to a new owner bills the **old** owner for the whole period; new owner billed £0 | Placement / invoicing |
| 4 | **Critical** | Ownership shares totalling < 100% silently under-bill the remainder | Ownership / invoicing |
| 5 | **Medium** | Per-owner rounding makes split amounts not reconcile to the full charge (no remainder handling) | Invoicing |
| 6 | **Medium** | "Unbilled charges" KPI counts the full amount of already-partially-invoiced split charges | Dashboard / finances |
| 7 | **Medium** | Manual invoice creation produces empty £0 invoices and burns an invoice number | Invoicing |
| 8 | **Low** | Invoice horse-group header shows `33% share` while the line shows `33.34% share` | Invoicing UI |
| 9 | **Low** | `get_unbilled_charges` has no lower date bound — stale charges sweep into any new invoice | Invoicing |
| 10 | **Low** | App loads fonts from Google Fonts CDN in `<head>`; no local fallback family | Front-end |
| 11 | **Low** | `/placements/` redirect is not itself login-gated (target is) | Access control |

**Verified working (no defect):** login/logout and login-gating on every app URL; role-based access control (non-staff "viewer" gets **403** on all create/edit/generate/send actions); placement overlap prevention; end-date-before-start validation; duplicate/overlapping-invoice prevention; split-charge `invoiced` deferral until all co-owners billed; PDF totals match the on-screen invoice exactly; full-month and mid-month-move day counts (inclusive) are arithmetically correct; responsive layout has **no horizontal overflow** at 375px on dashboard, horses, invoices, invoice detail, finances, placements, health.

---

## 1. Critical — Xero CSV export overcharges fractional-ownership livery lines

**Workflow:** Invoicing → download Xero CSV (`/invoicing/<pk>/csv/`, bulk `/invoicing/export-csv/`).

**Cause:** `invoice_to_xero_rows` (`horse_management/invoicing/utils.py:155-157`) emits `*Quantity = item.quantity` and `*UnitAmount = item.unit_price`. For **livery** lines those are *days* and the *full daily rate*, but the actual amount owed is the stored `line_total` (the owner's share). When the share is < 100%, `quantity × unit_price ≠ line_total`. Xero computes each line amount as Quantity × UnitAmount, so it ignores the correct figure.

**Steps to reproduce:**
1. Horse "Trio" owned 33.34 / 33.33 / 33.33 by three owners, on Premium (£7/day) for all of June (30 days).
2. Create Alice's June invoice → her Trio livery line is correctly £70.01 on screen and in the PDF.
3. `GET /invoicing/1/csv/`.

**Expected vs actual** (Trio livery line):
- Invoice/PDF `line_total`: **£70.01** ✅
- CSV row: `…,Premium £7.00 per day - 30 days …,30.00,7.00,…` → Xero computes `30 × 7.00 = ` **£210.00** ❌ (≈3× overcharge)
- Whole invoice: header `Total` is correct (£340.01) but Xero-computed line sum = 150 + **210** + 120 = **£480.00**, so the imported invoice bills £480 instead of £340.01.

Extra-charge and split-charge lines are **not** affected (their `unit_price` is already the share amount, quantity 1). Only livery lines with a share < 100% are wrong — i.e. every invoice for any fractional owner.

**Evidence:** `verify_final.py` TEST H output; `invoicing/utils.py:155-157`.

**Suggested fix:** For livery lines, export a share-adjusted unit amount (e.g. `UnitAmount = line_total / quantity`) or set `Quantity = 1, UnitAmount = line_total`. Simplest robust fix: emit `Quantity=1` and `UnitAmount=str(item.line_total)` for all lines.

---

## 2. Critical — Horses placed without an `OwnershipShare` are silently never billed

**Workflow:** Placement / monthly invoicing.

**Cause:** Livery billing is driven **entirely** by `OwnershipShare`: `InvoiceService.calculate_livery_charges` iterates `OwnershipShare.objects.filter(owner=owner)` (`invoicing/services.py:56-60`) and `get_owners_for_billing` selects owners `ownership_shares__…` (`services.py:306`). `Placement.owner` is ignored for billing. But only the "New Arrival" flow creates a share (`core/services.py:40`). These flows create a placement with **no** share: direct **Add Placement** (`PlacementCreateView`), single **Arrive** (`arrive_horse`), **bulk arrival** at a location (`bulk_arrive`/`log_arrival`), and **Add Horse** where the ownership formset is left empty. Such a horse is billed £0 for livery and its owner never even appears in monthly generation.

**Steps to reproduce:**
1. Horse "Ghost" placed for owner Emma on Grass (£5/day) for all of June via a direct placement (no ownership share).
2. Preview/generate June invoices.

**Expected vs actual:** Expected Emma billed 30 × £5 = **£150.00**. Actual: Emma has **£0** and is **excluded** from `get_owners_for_billing` (`verify_invoices.py`: "NOT billed: Emma Evans"). Silent revenue loss — no error, no warning.

**Evidence:** `verify_invoices.py` output; `invoicing/services.py:56-60, 300-317`; `core/services.py` (only `create_new_arrival` makes a share).

**Suggested fix:** Either (a) auto-create a 100% `OwnershipShare` whenever a placement is created for a horse that has none, or (b) fall back to `Placement.owner` in `calculate_livery_charges`/`get_owners_for_billing` when no shares exist, or (c) block placement creation until the horse has ownership shares totalling 100%.

---

## 3. Critical — Moving a horse to a new owner bills the old owner for the whole period

**Workflow:** Horse detail → **Move** (`/horses/<pk>/move/`, `MoveHorseForm` with optional `new_owner`).

**Cause:** `PlacementService.move_horse` (`core/services.py:61-116`) ends the old placement and creates a new one with `owner=new_owner`, but never updates the `OwnershipShare`. Because billing follows the share (see #2) and finds **all** placements for the horse, the old owner is billed for both the pre- and post-move placements, and the new owner (with no share) is billed nothing.

**Steps to reproduce:**
1. New arrival "MoveHorse" for **Old Owner** on 1 Jun, Grass £5/day.
2. Move to a new field on 16 Jun with **New Owner** selected.
3. Preview June invoices for both owners.

**Expected vs actual:** Expected Old Owner 1–15 Jun = 15 × £5 = **£75**, New Owner 16–30 Jun = **£75**. Actual: Old Owner **£150** (whole month, including days after the horse left them), New Owner **£0**.

**Evidence:** `verify_edge.py` TEST B — "Old owner billed £150.00 ; New owner billed £0"; ownership share still points to Old Owner after the move.

**Suggested fix:** When `move_horse` is given a `new_owner`, close/adjust the `OwnershipShare` as of the move date and open one for the new owner (or make billing period-aware of `Placement.owner`).

---

## 4. Critical — Ownership shares totalling < 100% silently under-bill

**Workflow:** Horse ownership (`/horses/<pk>/ownership/`) / invoicing.

**Cause:** `OwnershipShare.clean` (`core/models.py:642-655`) only rejects totals **> 100%**. A horse whose shares sum to less than 100% is allowed, and the unassigned percentage is never billed to anyone — livery and split charges are each multiplied by `share_fraction`.

**Steps to reproduce:**
1. Horse "HalfHorse" with a single 50% `OwnershipShare` (accepted).
2. Place on Grass £5/day for all of June; preview the owner's invoice.

**Expected vs actual:** The horse's full livery is 30 × £5 = £150. Owner billed **£75.00**; the other **£75 is never invoiced to anyone**.

**Evidence:** `verify_edge.py` TEST A.

**Suggested fix:** Require shares to total exactly 100% before a horse can be billed (validate in the ownership formset), or bill the un-shared remainder to a primary owner.

---

## 5. Medium — Split amounts don't reconcile to the full charge

**Workflow:** Invoicing, any horse with fractional owners.

**Cause:** Each owner's amount is `(full × share_fraction).quantize(0.01)` computed **independently** (`invoicing/services.py:73`, `150`). With shares like 33.34/33.33/33.33 the pennies don't sum back to the whole; there is no largest-remainder/reconciliation step.

**Expected vs actual:** Trio on Premium for June: full charge £210.00; owner shares billed £70.01 + £69.99 + £69.99 = **£209.99** (yard loses £0.01). Systematic across all uneven splits.

**Evidence:** `verify_invoices.py` reconciliation — "Trio placement Premium: full=210.00 split_sum=209.99 [MISMATCH]".

**Suggested fix:** Allocate the rounding remainder to one owner (e.g. the primary/largest share) so the splits always sum to the full charge.

---

## 6. Medium — "Unbilled charges" KPI counts partially-invoiced split charges in full

**Workflow:** Dashboard KPI + Finances "unbilled".

**Cause:** Both compute `ExtraCharge.objects.filter(invoiced=False).aggregate(Sum('amount'))` (`core/views/dashboard.py:117`, `core/views/finances.py:176`). A split charge keeps `invoiced=False` until **every** co-owner is billed (deferral is intentional — #verified working), but the KPI then counts the **whole** amount even though part is already on issued invoices.

**Expected vs actual:** After billing only Carol (60%) for the £81 farrier, £48.60 is already invoiced, yet the dashboard "Unbilled Charges" shows the full **£81** included (total £126 = £45 + £81). Overstates unbilled/forecast revenue.

**Evidence:** `verify_final.py` TEST G; mobile dashboard screenshot ("Unbilled Charges £126.00").

**Suggested fix:** Subtract already-invoiced line-item totals for split charges, or track a per-charge invoiced fraction.

---

## 7. Medium — Manual invoice creation makes empty £0 invoices and burns a number

**Workflow:** Invoicing → **Create invoice** (`/invoicing/create/`).

**Cause:** `invoice_create` calls `InvoiceService.create_invoice` directly with no zero-total guard. `generate_monthly_invoices` *does* skip zero totals (`services.py:346`), but the manual path does not.

**Steps to reproduce:** Create an invoice for an owner with no activity in the chosen period.

**Expected vs actual:** Expected a "nothing to bill" message. Actual: an invoice (e.g. `INV00003`) is created with **£0.00** and **0 line items**, consuming the next invoice number; it can then be *sent* to the owner.

**Evidence:** `verify_final.py` TEST F.

**Suggested fix:** In `invoice_create`, preview first and refuse (or warn) when total ≤ 0, mirroring monthly generation.

---

## 8. Low — Share % rounding inconsistent between group header and line

Invoice detail template (`templates/invoicing/invoice_detail.html:196`) renders the horse-group header with `{{ share|floatformat:0 }}` → **"Trio (33% share)"**, while the line description shows the true **"(33.34% share)"**. Same mismatch appears in the PDF group header. Cosmetic but looks like a data error to owners. Fix: use `floatformat:"-2"` (or match the description precision).

## 9. Low — `get_unbilled_charges` has no lower date bound

`get_unbilled_charges` filters only `date__lte=period_end` (`invoicing/services.py:118, 144`). Any never-invoiced extra charge from *before* `period_start` is pulled into a new period-specific invoice. Fine as a catch-all for monthly runs, but surprising when creating a one-off invoice for a specific historical period. Consider bounding by `period_start` (or making "sweep older unbilled" an explicit option).

## 10. Low — Google Fonts CDN dependency, no local fallback family

`templates/base.html:9-12` loads DM Sans / Source Sans 3 / JetBrains Mono from `fonts.googleapis.com`. When outbound access is blocked the request fails (observed as `ERR_CONNECTION_CLOSED` console errors on every page in this sandbox) and the UI drops to the browser default because the Tailwind config's font stacks have no generic fallback shown. Layout survives, but consider self-hosting the fonts or adding `sans-serif`/`monospace` fallbacks.

## 11. Low — `/placements/` redirect not login-gated

`/placements/` is a `RedirectView` to `/locations/?tab=history` declared before auth (`core/urls.py:49`); logged-out users get the redirect rather than a login bounce. No data is exposed (the target is gated), so this is cosmetic.

---

## Coverage notes & what I couldn't test

- **Tested:** auth/login-gating (all app URLs), role access control (staff vs viewer, 403s), horse/owner/location/placement lifecycle logic, overlap & date validation, the full invoicing math against a hand-computed ground truth (full month, mid-month move, fractional 60/40 and 3-way, mare+foal, zero-activity, direct vs split extra charges), duplicate-invoice prevention, PDF rendering & total parity, Xero CSV, dashboard/finances KPIs, reminder record-selection (vaccination due/overdue, farrier due), responsive layout 375/768/1280.
- **Not tested / limitations:**
  - **Email sending & Celery beat** — deliberately not run per safety rails; reminder *selection* logic reviewed and cross-checked against seeded due/overdue records, but actual `send_*` delivery and beat scheduling were not exercised.
  - **WeasyPrint PDF path** — not installed, so only the ReportLab fallback was tested; the `invoicing/invoice_pdf.html` template is effectively dead code in this build.
  - **Xero live OAuth API** (`xero_integration`) — not exercised; only the CSV export was tested.
  - **Debug Toolbar overlay** in screenshots and the `ERR_CONNECTION_CLOSED` console noise are `DEBUG=True` / sandboxed-network artifacts, not app defects.
  - Image/HEIC upload conversion and file-size validation were reviewed in code but not exercised with real uploads.

## Top 3 to fix first

1. **#1 Xero CSV overcharge on fractional livery lines** — it silently bills owners multiples of what they owe the moment the CSV is imported to Xero. Highest-impact, hardest to notice.
2. **#2 Placements without an ownership share are never billed** — silent, uncapped revenue loss; easy to trigger via ordinary "Add Placement"/"Arrive" flows.
3. **#3 Move-to-new-owner bills the wrong owner** — both owners' invoices are wrong after any change of ownership via Move.

(All three, plus #4, share one root cause worth addressing holistically: **livery billing keys off `OwnershipShare` while several workflows only maintain `Placement.owner`**, and shares aren't constrained to total 100%.)
