# Farrier page plan: test with our own farrier (Option A)

**Goal:** Our farrier records each visit on a phone in under 2 minutes. Yardway bills each owner on the monthly invoice. The yard pays the farrier one amount each month. Nobody types the same data twice.
**Scope:** One farrier, our own yard, our own horses. About 8 weeks of use after the build.
**Status:** Steps 1 to 7 built and live (step 7: simulated phone check done). Added: the yard's list (section 14), sign-in from the QR code by picking a name (section 15) and farrier notes (section 16). Next: the real-phone check at the yard, then step 8 (set-up with the farrier).
**Build estimate:** 7 to 9 working days, plus 1 day of set-up with the farrier.

---

## 1. Why we test this

This is the first test of the "visiting professionals" idea in
`docs/market/vet-app-market-model.xlsx`: one horse record, used by the yard and
by every professional who visits it. The farrier is the simplest professional
to test with:

- Visits every 6 to 8 weeks, so we get many visits in a short test.
- Simple work and prices: trim, front shoes, full set.
- No practice software to compete with.

The test answers 3 questions:

1. Will the farrier use a phone page on every visit?
2. How much admin time does it save the farrier and the yard?
3. Do the farrier bills reach the owners correctly, with no re-typing?

It does **not** test invoices in the farrier's name or immediate payment. That is Option B, later (section 11).

## 2. How it works, step by step

```
Farrier (phone)                         Yardway                                Yard office
-----------------                       -------                                -----------
1. Opens the farrier page   ──────────▶ Shows horses, due first
2. Taps horse + "Full set"  ──────────▶ Adds it to today's visit
3. Taps "Finish visit"      ──────────▶ Makes 1 farrier record per horse
                                        Makes 1 charge per horse (current owner,
                                        split between co-owners at invoice time)
                                        Sends the yard a summary email   ───▶  Checks the summary
                                                                                 (can fix a line)
Month end                               Monthly invoices include the farrier  ─▶ Sends invoices as now
                                        lines for each owner
                                        Farrier statement for the month       ─▶ Pays the farrier
                                                                                  Records it as a yard cost
```

## 3. What already exists (reuse, do not rebuild)

| Need | Already in Yardway | File |
|---|---|---|
| Farrier record per horse | `FarrierVisit`: date, work done, cost, provider, next due date (set to +6 weeks) | `horse_management/health/models.py:286` |
| Bill from a record | `sync_record_charge` makes or updates the `ExtraCharge` for a record. It never changes a charge that is already on an invoice. | `horse_management/health/views.py:333` |
| Charge text | `_charge_details`: "Farrier - Full Set", type `farrier`, provider | `horse_management/health/views.py:307` |
| Split between co-owners | `ExtraCharge.split_by_ownership` (on by default). The split happens when invoices are made. | `horse_management/billing/models.py:49` |
| Price list | `ServiceItem` with category `farrier` and a farrier work type | `horse_management/billing/models.py:417` |
| The farrier as a provider | `ServiceProvider` with type `farrier` | `horse_management/billing/models.py:17` |
| Who is due | Farrier due dates in the attention list | `horse_management/core/dashboard/attention.py` |
| Many horses at once | The bulk health form (staff use) | `horse_management/health/views.py:514` |
| Access control | `Role` with a feature map, `has_feature_access`, `feature_required` | `horse_management/core/models.py:1773`, `horse_management/core/permissions.py` |
| Monthly invoices | `InvoiceService.generate_monthly_invoices` takes unbilled charges | `horse_management/invoicing/services.py` |
| Paying a supplier | `YardCost` with category "Professional Services" | `horse_management/billing/models.py:229` |
| Change history | django-simple-history and `AuditEvent` | `horse_management/core/audit.py` |

## 4. Why not just give the farrier the staff pages

The current roles are too wide for a person from outside the yard:

- **Health = Full** shows every health record, including vet visits and medical conditions.
- **Charges = Full** lets a user add any charge to any owner.
- The staff pages show owners' names, contact details and money.

So the farrier gets a **separate page** with its **own access key**. It shows only what the farrier needs.

## 5. What the farrier sees

The page is built for a phone first: big buttons and one column. It uses the existing app layout (PWA).

### Getting to the page: the yard QR code

- A printed QR code hangs on the tack room door (and, if useful, in the feed room).
- The farrier scans it with the phone camera. It opens the farrier page.
- **First time:** the farrier signs in, then adds the page to the phone's home screen. After that, the farrier taps the icon or scans the code.
- **Next times:** the phone stays signed in (section 9), so the scan goes straight to Today's visit.
- The QR code holds only the page address. It holds no password or key. A person who scans it and has no farrier login sees the normal sign-in page and nothing else.
- Yardway makes the code. Staff open **Settings → Users → Farrier → Print QR code**. This uses the `qrcode` library that two-step sign-in already uses, so there is no new package.

### Screen 1: Today's visit (the main screen)

- Header: "Farrier visit · 10 Nov 2026" and a counter "3 horses · £255".
- Search box: type part of a horse's name.
- Location filter (field or stable), because the farrier works at one place at a time.
- **Due now** (at the top): horses that are overdue or due within 14 days. Each row shows:
  - photo (thumbnail), name, location
  - last work and date, for example "Full set · 29 Sep"
  - a due label: "Due" or "Overdue 5 days".
- **Other horses** (folded): every other active horse.

Tap a horse. A small panel opens with the farrier's price buttons:

```
 [ Trim £35 ]  [ Front shoes £75 ]  [ Full set £95 ]
 [ Remedial … ]  [ Remove shoes £20 ]  [ Other … ]
 Note (optional): ____________________
```

- One tap adds the line to today's visit. The row shows a tick and the work.
- "Other" or "Remedial" opens a price box and a short description.
- The farrier can change the price for one horse (for example, an extra charge for a hard-to-shoe horse).
- Tap the row again to change or remove the line.

### Screen 2: Finish visit

- A list of every line: horse, work, price, note. Total at the bottom.
- **Finish visit** button. It asks once: "Send 6 lines (£510) to the yard?"
- After this: "Sent to the yard. You do not need to send a bill." It shows each horse, the work, the price and the next due date, and a link to "My visits".
- **Summary message to the farrier:** an email straight after Finish, so they can move on: each horse, the work, the price, any note, the next due date, and the total. It never names owners. It goes to the provider's email, or else the login's email. (Added 10 Nov 2026 at the user's request.)

### Screen 3: My prices

- The farrier's own price list: name, work type, price.
- Add, edit, hide. These are `ServiceItem` rows linked to this farrier.

### Screen 4: My visits

- Past visits by date, with lines and totals.
- Each month's total: "November: 18 horses · £1,530 · Paid / Not paid yet".
- The farrier can change a line on the **same day**, before the yard checks it. After that, the farrier asks the yard.

### What the farrier never sees

Owners, owner contact details, invoices, payments, vet records, medical conditions, other charges, other providers, settings. A direct link to any staff page sends the farrier back to Today's visit.

## 6. What the yard office sees

- **Email after each visit:** "Farrier visit: 6 horses, £510", with a link to the visit.
- **Visit page (staff):** each line, with a status:
  - "Billed to [owner]"
  - "No owner: not billed" (the warning text already exists in `CHARGE_SYNC_MESSAGES`)
  - "On invoice INV-…".
- Staff can fix a line before it goes on an invoice. This uses the normal farrier record edit page.
- **Farrier statement (monthly):** every line for the month, the total, and a button **Record payment to farrier**. The button makes one `YardCost` (category "Professional Services", supplier = farrier name, amount = total) and marks the month as paid.
- **Settings → Users:** a "Farrier" role, and the farrier's login linked to their `ServiceProvider`.

## 7. Data changes

All changes are small and add to existing tables. Each needs one migration.

| Change | Model | Why |
|---|---|---|
| `user` (one-to-one, can be empty) | `ServiceProvider` | Links the farrier's login to their provider record. Every record and charge they make gets the right provider. |
| `provider` (foreign key, can be empty) | `ServiceItem` | The farrier's own price list. Items with no provider stay the yard's list, as now. |
| New model `ProviderVisit`: provider, date, started at, finished at, created by, status (open, finished), checked by yard (date, user), paid (date, linked `YardCost`) | `health` app | Groups one day's lines into one visit. Gives the timings we measure. Holds the paid status for the statement. |
| `provider_visit` (foreign key, can be empty) | `FarrierVisit` | Links each farrier record to its visit. |
| New feature key `farrier_portal` (Hidden / Full) | `core/features.py` | The access key for the farrier pages. Staff roles keep it hidden. |
| Seed role "Farrier" | data migration | Only `farrier_portal` = Full. Everything else hidden. |

We do not change `ExtraCharge` or the invoice code. The farrier lines use the same path as farrier records made by staff today.

## 8. Rules the build must follow

1. **One path for billing.** Each line makes a `FarrierVisit`, then calls `sync_record_charge`. Do not make `ExtraCharge` rows by hand.
2. **Bill the current owner.** If a horse has no current owner, keep the record, do not bill, and show the line as "No owner: not billed" to the yard. The farrier sees "Saved".
3. **Never change billed money.** A line that is already on a live invoice cannot be changed or removed from the farrier page. This uses the existing `on_live_invoice` rule.
4. **No double lines.** "Finish visit" runs once, in one database transaction. It locks the visit row, so a double tap or a slow network makes one set of records, not two.
5. **The farrier sees only their own data.** Every query on the farrier pages filters by the farrier's `ServiceProvider`. Tests must try to read another provider's visits and prices, and must fail.
6. **Active horses only.** The horse list shows active horses at the yard. It never shows owners.
7. **Prices are the farrier's.** The price buttons come only from `ServiceItem` rows linked to this farrier.
8. **Every change is logged.** Use the existing history and audit tools.

## 9. Sign-in and security

- **Login:** the farrier gets a normal Yardway login (email and password). Brute-force lockout (django-axes) already applies.
- **Two-step sign-in:** today it is required for admins only (`MFA_REQUIRED_FOR_ADMINS`). For the test, the farrier role does not need it. Decide before Option B (section 12, question 4).
- **Stay signed in:** a long session on the farrier's phone (for example 30 days), so the farrier does not type a password in a field. Only for the Farrier role.
- **Personal data:** the farrier sees horse names, photos and locations only. Add the farrier as a data recipient in `docs/DATA_RETENTION.md`.
- **QR code:** it holds only the page address, not a login. If the code is copied or photographed, nobody gets in without the farrier's password. If we end the farrier's access, the code stays on the door and does nothing.
- **Agreement:** a short written note with the farrier: what they can see, what they must not share, and how we end access.

## 10. Build steps

| # | Step | Days |
|---|---|---|
| 1 | Data: `ServiceProvider.user`, `ServiceItem.provider`, `ProviderVisit`, `FarrierVisit.provider_visit`, feature key, Farrier role. Migrations and model tests. | 1 |
| 2 | Access: `farrier_portal` gate; send the farrier role to Today's visit from every other page; scope every query by provider. Tests that try to cross the scope. | 1 |
| 3 | Screen 1, Today's visit: horse list, due first, search, location filter, price panel (HTMX, same pop-up sheet as the bulk form). | 2 |
| 4 | Screen 2, Finish visit: one transaction, records and charges through `sync_record_charge`, yard email. Tests: co-owned horse, no owner, double tap, price change. | 1 |
| 5 | Screens 3 and 4: My prices, My visits, same-day edit. | 1 |
| 6 | Yard side: visit page with line status, farrier statement, "Record payment to farrier", and "Print QR code" (an A5 sheet with the code and the yard name). | 1 |
| 7 | Check on a real phone at our yard (mobile QA script in `horse_management/tools/qa/`), fixes, `docs/DATA_RETENTION.md` update. | 1 |
| 8 | Set-up with the farrier: put the QR code on the tack room door, first sign-in, add the page to the home screen, enter the price list, a 10-minute show-and-try at the yard. | 1 (with the farrier) |

Before each push: `ruff check .`, `python manage.py makemigrations --check --dry-run`, and the test suite, as in the README.

## 10a. Step 7: phone check results

**Simulated phones** (`horse_management/tools/qa/farrier_phone_qa.js` on a `farrier_seed.py` yard): iPhone SE (320 px wide) and iPhone 13 (390 px). 12 screens each: sign-in, Today, search, horse panel, Today with lines, Review, Finished, My visits, a visit, Change a line, My prices, Add a price. Then a staff page, to check the fence.

| Found | Fixed |
|---|---|
| A first visit (no farrier records yet) showed "No horses due" and every horse folded under "Other horses". The page looked empty. | "Other horses" opens when no horse is due. |
| The location filter was cut to "All pla" on a small phone. | Search and the filter are now one full-width row each. |
| Tap targets under 44 px: Sign out, Change, Hide/Show, Remove, Back links, the two fold-out headings, the note, price and work fields. | All at least 44 px high. |
| The farrier was signed out after 7 days without use, so a password at most visits. | A farrier login now idles for 30 days (`PROVIDER_SESSION_IDLE_AGE`). The 30-day cap from sign-in stays. |

After the fixes: no screen is wider than the phone, no tap target is under 44 px, no server errors, and a staff page sends the farrier back to `/farrier/`.

`docs/DATA_RETENTION.md` now lists the farrier as a recipient of personal data and says what they see.

**Still to do on a real phone at the yard** (the simulation cannot test these):

1. Scan the printed QR code with the phone camera. It opens the sign-in page.
2. Sign in, then add the page to the home screen (Safari: Share, Add to Home Screen; Chrome: menu, Add to Home screen). Open it from the icon.
3. In the place with the weakest signal at the yard: tap a horse, tap a price. Note how long the tick takes to show.
4. Do 5 horses. Time it from the first tap to Finish visit (target: under 2 minutes).
5. Read the screen in bright daylight, and tap with gloves on, if the farrier wears them.
6. Check the summary email arrives on the phone.
7. Next day, open the icon again: it must not ask for the password.

Write down anything slow or hard to read. Small fixes go in before step 8.

## 11. The test: how we measure it

**Before the build (week 0):** time 1 normal farrier visit at our yard:

- the farrier's admin after the visit (writing up, sending the bill)
- the yard's admin (typing the farrier bill, splitting it, putting it on invoices).

**During the test (8 weeks, about 1 to 2 rounds of every horse):**

| Measure | How we get it | Target |
|---|---|---|
| Visits recorded on the page | `ProviderVisit` count vs. visits on the farrier's own diary | Every visit |
| Time on the page per visit | `started at` to `finished at` | Under 2 minutes for 10 horses |
| Farrier admin after a visit | Ask the farrier each month | Close to zero |
| Yard admin for farrier bills | Time it, as in week 0 | At least 80% less |
| Lines the yard had to fix | Lines edited by staff after "Finish" | Fewer than 1 in 20 |
| Owners billed correctly | Owner questions about farrier lines | None |
| Would the farrier keep it? | Ask at week 8 | Yes, and says what to change |

**Pass:** the farrier uses it on every visit, and both sides save real time.
**Then:** run the same test with Tim (vet), and plan Option B.

## 12. Decisions and open questions

Decided on 10 Nov 2026:

1. **Price:** the yard bills owners the same price the farrier charges. The yard makes no margin.
2. **Phone signal:** good in every field and stable. The page does not need to work offline.
3. **VAT:** the farrier is not VAT registered. No VAT on farrier lines or on the `YardCost`.

Still open:

4. **Two-step sign-in** for the farrier: required or not during the test?
5. **Owners:** tell owners that farrier lines will appear on the monthly invoice, if they do not already.
6. **Horses the farrier must not see:** for example, horses in a closed stable. The plan shows all active horses.

## 13. Not in this test (later, Option B)

- Invoices in the farrier's name, with the farrier's own numbering and bank details.
- An invoice to the owner right after the visit, and a pay-now link (Stripe Connect, see `docs/payments/payments-plan.md`).
- One farrier working at many yards (needs the multi-yard rebuild).
- Vets, dentists and physios (same pattern, after this test).

## 14. The yard's list for a farrier visit

The yard manager makes a list for a visit date: the horses, the work wanted (or "farrier to decide") and a note.

- **Where:** Health → Farrier → **Farrier's list**, or the **Farrier's list** button on Farrier Visits (`/health/farrier-visits/list/`). Health: view to see it, Health: full to change it.
- **The farrier:** on that date, the horses on the list are at the top of **Due now**, tagged **On the list**, with the yard's request under the name. In the horse's panel, the price button for the work wanted is outlined.
- **Done:** an item is done when the farrier records a line for that horse on that date. The list page shows each item as **To do**, **Done** (with the work and price) or **Not done** (after the date).
- **Data:** `FarrierJob` (provider, date, horse, work wanted, note), one per horse per day.

The farrier page shows **Due now** (the yard's list, then horses due within 14 days) and **All other horses**, open, below. Search finds any active horse.

## 15. Sign-in from the QR code: pick your name, no password

**Decision (the yard owner, November 2026):** the farrier scans the QR code, picks their name from a dropdown and is signed in. No password and no PIN.

- **Who can be picked:** only an active farrier in Service Providers with a linked **farrier-only** login. A staff login is never offered, so the QR code cannot sign anyone in as staff.
- **What such a session can do:** the farrier page only (the fence in section 4). If the login is later given a staff role, the session stops working (`core.auth_backends.FarrierQRBackend`).
- **Audit:** each sign-in is in the audit log as "Signed in · QR code, name picked", with the address it came from.
- **The risk the yard accepts:** anyone who has the QR code (a photo of the door is enough) can sign in as the farrier. They could then record work, which adds charges to owners' next invoices, and see horse names and fields. The yard checks every visit before it is paid ("Mark as checked"), and a wrong line can be removed. **To close this later:** add a 4-digit PIN per farrier (the screen already has room for it), or make the picker work only on a phone that has signed in once.

## 16. Farrier notes on a horse

- **With the work:** each line has "Notes for the yard": why the work was done, issues, suggested treatment (up to 1,000 characters). It is saved on the farrier record.
- **On its own:** "Note only, no work or charge" saves a `FarrierNote` on the horse. It makes no charge and does not change the farrier due date. It joins the day's visit when the farrier taps Finish.
- **Where the yard sees them:** on the horse's page (the timeline: "Farrier", and "Farrier note"), in the horse preview, and on the visit page under Farrier Visits.
