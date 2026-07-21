# Yardway — Full Codebase QA Review (21 July 2026)

> **Resolution status (same day):** every critical (C1–C3), high (H1–H10)
> and medium (M1–M19) finding below has been fixed on this branch, along
> with the vast majority of the low-severity list — see the commit history
> following this report. Not addressed (product-level decisions):
> per-owner reminder digests, a credit-note/overpayment mechanism, Celery
> task time limits/EMAIL_TIMEOUT, pagination of the Active horses tab,
> moving monthly invoice generation out of the web request, the
> `*.vercel.app` CSRF wildcard (required for preview deploys), and the
> PDF group-header share % cosmetic. Vercel's ephemeral media (M10) is
> infrastructure — documented as a rollback-window warning in CUTOVER.md.

Scope: entire application — core lifecycle, invoicing/billing, health/notifications,
roles/permissions, Xero integration, deployment config, and all 126 templates/UI
workflows. Method: six parallel deep code reviews plus dynamic testing (full test
suite, fresh-DB migrate, an authenticated crawl of all 313 URLs, and an anonymous
crawl of every endpoint). Headline findings were reproduced against a live test
instance before being reported.

**Baseline health:** 413 tests all pass; fresh `migrate` from an empty DB works;
no URL returns a 500 as admin; anonymous access is limited to login/password-reset/
health-check; `check --deploy` is clean; no dependency conflicts; no secrets in
the repo or its history.

Findings are numbered and ordered by severity. File references are to the state
of `main` at commit `688a3b2`.

---

## CRITICAL

### C1. Nobody can sign out — logout links use GET, Django 5 LogoutView is POST-only
`templates/base.html:192` (desktop) and `:391` (mobile) render `<a href="{% url 'logout' %}">`.
Django 5.x `LogoutView` only accepts POST. **Reproduced: `GET /accounts/logout/` → 405.**
- Desktop: the link is hx-boosted, htmx receives the 405 and swaps nothing — the button silently does nothing.
- Mobile: `hx-boost="false"`, so users land on a raw "405 Method Not Allowed" page.

Fix: replace both anchors with a POST form (with `{% csrf_token %}`).

### C2. Pending Departures widget can bulk-depart horses that are still on the yard
`core/views/dashboard.py:192-197` selects `Placement.objects.filter(end_date__lte=today, horse__is_active=True)`
— i.e. **every historical closed placement of every active horse**. Every field move
creates such a row (`move_horse` closes the old placement at `move_date − 1`), so any
horse that has ever moved appears under "Pending Departures" forever.
- Clicking **"Confirm all N"** posts them to `PlacementService.confirm_departure`
  (`core/services.py:298-311`), which defensively closes the horse's *live* placement
  at today and sets `is_active=False`. One click departs every horse that has ever
  moved fields. **Reproduced.**
- The per-row ✕ (`cancel_departure`) cannot clear the row: the horse has an open
  placement so the service no-ops (`core/services.py:324-327`) — the stale row is
  permanent.
- Only mitigation: the widget is hidden by default (`core/dashboard_widgets.py:43`),
  but stored user preferences win.

Fix: the query must select only horses whose **latest** placement is closed and who
have **no open placement** (i.e. exclude `placements__end_date__isnull=True`).
The tests added with the "honest confirm buttons" commit (`core/tests/test_placement_lifecycle.py:903-950`)
only cover horses with a single closed placement — add a moved-horse regression test.

### C3. Pressing "Cancel" on delete confirmations still deletes the record
The vendored htmx 1.9.10 boost handler never checks `event.defaultPrevented`, so
`onsubmit="return confirm(...)"` only blocks the native submit — the boosted POST
fires regardless. Affected destructive forms (all inside the app-wide `hx-boost`):
- `templates/invoicing/invoice_detail.html:300` — **remove payment** (alters invoice balance/paid status)
- `templates/placements/placement_form.html:121` — delete placement ("This cannot be undone")
- `templates/settings/role_form.html:113` — delete role
- `templates/documents/_documents_card.html:34` — delete document
- `templates/horses/_photo_grid_card.html:29` — delete photo

Fix: use `hx-confirm` (as `pending_departures.html` already does) or `hx-boost="false"`
on these forms. Related (lower severity): `billing/charge_list.html:121,174` and
`billing/costs_list.html:198` have decorative confirms on boosted anchors.

---

## HIGH

### H1. Success/error messages are never shown after any boosted action
`templates/base.html:219-242` — the toast container sits outside `#main-content`,
but body-level `hx-boost` swaps only `#main-content` (`base.html:59`). Django's
message storage is consumed by the discarded part of the response, so messages are
lost forever. Worst case: "3 invoices failed to send. Check email configuration."
(`invoicing/views.py:529-547`) is never seen. Fix: move messages inside the swap
target, or emit them via `HX-Trigger`/OOB swap.

### H2. A partial-period invoice permanently suppresses the rest of that month's livery
`invoicing/services.py:34-45` + `:517-521` — livery idempotency is *any overlap on
the period*, and no per-day billed marker exists. A manual invoice for 1–10 June
(e.g. settling up a departing horse) makes the 1 July monthly run skip that owner
entirely — the other horses' 30 days of June livery are **never billed**, and the
run reports it as "Skipped (already invoiced)". Extra charges are unaffected (they
carry `invoiced=False`); only livery is lost.

### H3. Health "overdue" lists/counts include every historical record — permanent false overdues
`health/views.py:97-126` (overview), also `:243-244`, `:265-266`, `:664-665`, `:822-823`
— filters like `next_due_date__lt=today` with no latest-per-(horse, type) restriction.
The day after a horse's annual re-vaccination, last year's record makes it "overdue"
in Action Required; farrier (6-week cycle) adds ~8 phantom rows per horse per year.
Lists are materialised unpaginated, so the page degrades monotonically. The farrier
*reminder task* already implements the latest-only guard (`notifications/tasks.py:76-101`)
— the dashboard never got it.

### H4. Vaccination reminder emails fire for superseded records
`notifications/tasks.py:36-59` — unlike the farrier task, no check for a newer
vaccination of the same type:
- Re-vaccinate early → the old record's reminder window still opens → owner gets a
  "Vaccination Due" email for a horse just done.
- Backfilling history (onboarding a yard) → every historical record has
  `reminder_sent=False` and a past due date → **one spurious email per historical
  record at the next 07:00 run** (hundreds in one morning).

### H5. Xero connection self-destructs on any transient token-endpoint failure; refresh has no concurrency control
`xero_integration/client.py:121-127` — any non-200 (429, 5xx, proxy blip) is treated
as "refresh token expired": `is_active=False`, integration dead until an admin
re-runs OAuth. No `select_for_update`/re-read around refresh, while gunicorn
(2×4), Celery worker and beat can all refresh concurrently with the same rotated
refresh token.

### H6. Xero invoice push is not idempotent or transactional — timeouts create duplicate invoices in Xero
`xero_integration/services.py:131-191`:
- No `Idempotency-Key` header sent (`client.py:151-190`).
- `requests` Timeout/ConnectionError after Xero created the invoice propagates
  uncaught → no sync record; retry then fails on duplicate `InvoiceNumber` → sync
  stuck at ERROR with no `xero_invoice_id`, no reconciliation path → accountant
  re-keys it → duplicate in Xero.
- "Already pushed" check is check-then-act; a double-click double-POSTs and the
  loser's OneToOne `IntegrityError` is uncaught.
- Bulk loop (`invoicing/views.py:596-606`) catches only `XeroAPIError` — a network
  error mid-batch 500s the request.

### H7. Vercel WSGI entrypoint leaks a full Python traceback publicly on boot failure
`wsgi.py:39-51` (repo-root copy — the one `vercel.json` routes to). The JSON error
body includes the complete `traceback.format_exc()` (paths, settings internals,
env-var names). Vercel remains live/rollback target per CUTOVER.md. Also related:
migrations run at import time on every cold start (`wsgi.py:31-33`) and can race
across concurrent lambda instances.

### H8. Departed tab count is wrong for any horse that ever moved
`core/views/horses.py:164-173` — `Count('pk', filter=~Q(placements__end_date__isnull=True))`
applies per-joined-row, not NOT-EXISTS (the list queryset at `:118-122` uses correct
subquery semantics). **Reproduced:** one mover → badge "Departed 1", tab shows 0 rows.
Badge inflates by every horse that ever moved.

### H9. A future-dated departure makes the horse vanish from all "current" views immediately
`core/services.py:287-292` closes the placement at the future date; from that moment
the horse has no open placement → drops off the Active tab, its field's Current
Horses, capacity/availability counts and the Total Horses KPI — while it is still
physically on the yard and billing continues to the future end date. Nothing lists
it as "leaving soon" either (Upcoming Departures keys off `expected_departure`).

### H10. Re-arrival with a different owner doesn't move the ownership share — costs bill the old owner
`core/services.py:224-265` (`arrive_horse`/`_place`) never syncs `OwnershipShare`
(unlike `move_horse`, `:129`), though the arrival forms offer an owner picker.
`Horse.current_owner` prefers the share, and health/billing charges bill
`current_owner` → horse sold and returns under a new owner: livery bills correctly,
but the next farrier/vaccination cost is invoiced to the **previous** owner.

---

## MEDIUM

### Billing gaps
- **M1. Split charges on departed co-owned horses are never auto-billed.**
  `invoicing/services.py:463-497` — owner discovery misses split charges on co-owned
  horses with no placement in the period. A vet bill entered after the horse's final
  invoice sits in `unbilled_total()` forever unless someone manually invoices the owner.
- **M2. Health-cost → charge sync cluster** (`health/views.py:402-433`, `billing/views.py:148-175`,
  `health/models.py:68-74`):
  - Horse with no current owner → `sync_record_charge` returns silently: cost recorded,
    **never billed**, no warning (e.g. final worming logged after departure).
  - Deleting an uninvoiced charge in Billing is undone by any later edit of the health
    record — the charge is silently recreated (and manual charge-amount edits are
    stomped back to `record.cost`).
  - Zeroing/blanking a record's cost leaves the charge alive at **£0.00**, which lands
    as a £0.00 line on the next invoice.
  - Editing a record's cost after invoicing succeeds silently — record and invoice
    permanently disagree, difference never billed/credited.
  - Deleting a health record via Django admin orphans its uninvoiced charge (still billable).
- **M3. Feed-out recharge silently absorbs the share of owner-less horses** and can
  drop the rounding remainder with it (`billing/views.py:661-683`). The two billed
  owners are not re-spread; no message.
- **M4. Feed costs are double/triple-counted in cost reporting** — purchase creates a
  YardCost, feeding out creates a second YardCost from the same stock, recharging
  creates ExtraCharges; `CostsListView` sums them all (`billing/views.py:340-349,
  528-542, 648-658`). £500 spent reports as ~£1,500. Owner billing unaffected.

### Permissions / data exposure
- **M5. Owner detail leaks Finance data to Finance-hidden roles.** `core/views/owners.py:83-86`
  + `templates/owners/owner_detail.html:207-289` render invoices and unbilled charges
  with no `feature_access.invoices/charges` guard (nav is correctly gated). Same class,
  smaller: `horse_detail.html:529/542` shows vet/farrier £ costs to any `horses: view` user.
- **M6. Uploaded media is login-gated only.** `horse_management/urls.py:46-59` — any
  authenticated user, regardless of role, can fetch passports, insurance docs and
  receipts at guessable `/media/...` paths (Railway `SERVE_MEDIA=True` config).

### Xero divergence
- **M7. One-way sync leaves local and Xero state diverged:** local cancel never voids
  the Xero copy (and drops it from the nightly sweep); Xero VOIDED → local invoice
  stays SENT/OVERDUE **and keeps sending overdue reminders**; Xero DELETED is
  unhandled (badge lies forever); local mark-paid never reaches Xero
  (`invoicing/views.py:159-184`, `xero_integration/services.py:205-220`, `tasks.py:37-41`).
- **M8. VAT: tax code hardcoded to OUTPUT2 for any non-zero rate** (`services.py:90`)
  and local per-invoice VAT rounding vs Xero's per-line rounding → 1p total mismatches
  (3 × £33.33 → £119.99 local vs £120.00 Xero). Same defect in the CSV export
  (`invoicing/utils.py:122-171`), where Xero flags rows whose `Total` disagrees.
- **M9. No 429/Retry-After handling** — the nightly status sweep hammers on through
  rate limits; unchecked invoices mean owners who paid in Xero still get overdue
  reminders that morning (the sweep-before-reminders ordering is defeated).

### Deployment
- **M10. Vercel media is broken/ephemeral** (read-only FS, nothing serves `/media/`)
  — uploads made while Vercel serves are lost; it remains the rollback target.
- **M11. Silent SQLite fallback when `DATABASE_URL` is unset/typo'd**
  (`settings.py:168-174`) — app boots against ephemeral SQLite and loses everything
  on redeploy instead of failing loudly.

### Core / UX correctness
- **M12. Location edit 500s after a partial save** if usage changed the same day the
  current usage period started (`core/views/locations.py:372-395` doesn't catch the
  `ValidationError` that `set_usage` raises; other fields already saved). Reproduced.
- **M13. "Today" is the UTC date, not the London date**, across ~29 call sites in
  core plus health/notifications (`timezone.now().date()`/`date.today()` instead of
  `timezone.localdate()`). Between 00:00–01:00 BST, "today" is yesterday: departures
  dated today don't deactivate, pickers pre-fill yesterday, due/overdue shift a day.
- **M14. Pagination rebuilds query strings without urlencoding**
  (`templates/includes/pagination.html:10,19`, `htmx_pagination.html:12,28`,
  raw echoes in `horse_list.html`) — search "Tom & Jerry" + Next → page 2 searches "Tom".
  Affects all 12 paginated lists.
- **M15. Invoice select-all double-counts** — hidden mobile checkboxes + visible
  desktop ones are both counted (`invoice_list.html:113-192`): 12 selected shows
  "24 invoices selected" right before Send/Mark-paid. (POST itself dedupes.)
- **M16. Departed-tab search results beyond 25 are unreachable** — paginated but the
  search branch renders no pager (`core/views/horses.py:71-75`, `horse_list.html:140-264`).
- **M17. manifest.json cached a year with no cache-buster** (`base.html:11` uses
  `static`, not `static_v`; `WHITENOISE_MAX_AGE=31536000`) — the CGate→Yardway
  rebrand won't propagate to existing PWA users for up to a year.
- **M18. Locations list N+1 + two disagreeing occupancy numbers on one card** —
  template uses the per-location `current_horse_count` property despite the view
  annotating `horse_count`, and the two disagree on stranded horses
  (`templates/locations/location_list.html:349,355`, `core/views/locations.py:131-139`).
- **M19. Scale hotspots:** fuzzy search iterates the entire placements table in
  Python per search/keystroke (`core/search.py:53-76`, `quick_find`); the Active
  horses tab is deliberately unpaginated and renders every horse twice
  (`core/views/horses.py:71-75`); monthly invoice generation runs all pricing twice
  with per-owner query fan-out synchronously in the web request
  (`invoicing/services.py:499-532`) — thousands of queries at ~150 owners.

---

## LOW (abridged)

- Monthly-generation race: concurrent beat run + manual click → uncaught
  `DuplicateInvoiceError` aborts the batch mid-way (re-run is safe) (`invoicing/services.py:516-531`).
- Concurrent arrival/move submissions for the same horse → unhandled `IntegrityError`
  500 (data protected by constraint; UX only) (`core/views/horses.py:497-516`).
- `placement_delete` / `cancel_departure` silently rewrite billed history with no
  invoiced-state warning; bulk "restore" can re-open a 2024 placement and make the
  whole current period chargeable (`core/views/placements.py:102-124`, `core/services.py:315-337`).
- Legacy `HorseOwnership` model still editable in admin but ignored by all billing
  (silent trap); admin bulk-delete of placements bypasses lifecycle hooks.
- Missing `(location, end_date)` index on Placement; departed-tab location/owner
  filters return nothing by construction (`core/views/horses.py:133-152`).
- Reminder claim leaks if the send *raises* (vs returns False) — reminder silently
  consumed (`notifications/tasks.py:49-61`); owner-with-no-email records retried
  forever and can fire stale years later; no Celery task time limits / EMAIL_TIMEOUT;
  broker outage at 07:00 silently skips the day.
- `reminder_sent` never re-armed when a due date is edited (documents already solve
  this; health records don't) — pushed-out due dates get no reminder (`health/models.py:75,165`).
- `next_due_date` not recalculated when `date_given`/type is edited (`health/models.py:98-103`).
- Deactivated vaccination types remain selectable in record forms (`health/forms.py:63-72`).
- Future-dated health records and `interval_months=0` / `reminder_days_before=0`
  accepted without validation.
- Beat stagger `(REMINDER_MINUTE + 5) % 60` wraps without hour carry (`settings.py:318-341`).
- Per-record reminder emails, no per-owner digest.
- Xero: exact-name contact matching — two owners with the same name can never both
  sync (uncaught `IntegrityError` / permanent 400); status badge swallows all errors
  (`views.py:183-185`); `xero_connect` mutates state on GET; invoice date uses UTC
  date (`services.py:122`) — midnight-BST invoices land in the previous day/period.
- `*.vercel.app` wildcard in ALLOWED_HOSTS/CSRF_TRUSTED_ORIGINS defaults (blunted by
  SameSite=Lax); `.env.example` ships `DEBUG=True`; console email backend is the
  prod default (sends "succeed" into logs, invoices marked SENT).
- Quick-add photo path bypasses Pillow content validation (extension allowlist still
  blocks XSS-capable types) (`core/views/photos.py:62-78`).
- Finances chart "forecast" annotation silently ignored — vendored Chart.js has no
  annotation plugin (`templates/finances.html:136-148`).
- Invoice list empty-state ignores date filters; global submit-spinner disables the
  wrong button on the six health forms with two submits and re-arms after 8s;
  per-row invoice "Send" emails with a single tap, no confirm, no feedback (see H1);
  horse-detail Quick Actions not feature-gated (server gate correct — users just
  get a 403 page).
- PDF/invoice-detail horse group header shows the livery share % against 100%-billed
  extra lines (`invoicing/pdf.py:186-187`) — misleading, not wrong-money.
- Stale comment: `test_settings.py` claims fresh `migrate` is broken; it now works.
- Cost edits after invoicing show a plain success with no "already invoiced" warning (UI courtesy of M2).

---

## Verified clean (highlights)

- **Money discipline:** Decimal end-to-end; inclusive day counting consistent
  everywhere; move day never double-billed; ownership-split penny drift reconciled
  to the primary contact (tested); invoice numbering under `select_for_update`;
  idempotent monthly generation with owner-row locking; zero-total invoices never created.
- **Placement integrity:** overlap prevention holds through forms, services, admin
  and history edits (partial unique constraint + unconditional `full_clean`);
  PROTECT on locations/owners/rate types — no cascade destruction of financial history.
- **Role Suite core:** ordered access ladder correct (`full` ⇒ `view`), fail-loud on
  unknown features, all-hidden defaults, no stale caching, last-admin lockout closed
  on all four paths, deactivation kills sessions, no CSRF exemptions, no open
  redirects, no `|safe` on user data, every write endpoint server-side gated
  (confirmed by anonymous crawl: only login/password-reset/health-check public).
- **The recent fix commits hold:** the Xero gate bypass (f35002b) is fully closed;
  the placement lifecycle choke point (d340698) is sound including rollback paths;
  the rebrand sweep (03fac8e) broke no URLs, includes, or static refs — all 112
  `{% url %}` names resolve with correct argument counts.
- **Ops:** SECRET_KEY has no default; DEBUG defaults off; HSTS/secure cookies/frame
  deny in prod; Railway shape sound (pre-deploy migrate, volume media, WhiteNoise);
  reminder tasks use atomic claim-then-send with tested rollback; EHV/farrier guards
  correct; no secrets anywhere in the repo or history.
