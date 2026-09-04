# Bug audit — September 2026

Branch `claude/bug-audit-hardening-yh24w9`. Baseline before the audit: 757 tests passing.
Each entry says what was wrong, what changed, and how to check it. Every fix has a
regression test; file names are given so you can run just that test.

Run one test module with:

```bash
cd horse_management
DJANGO_SETTINGS_MODULE=horse_management.test_settings python manage.py test <module>
```

## Fixes, in priority order

### 1. Xero connection died permanently after a failed invoice push
- **Symptom:** After any push that failed while a token refresh happened, the next refresh got `invalid_grant` and Xero showed as disconnected until someone reconnected.
- **Cause:** `push_invoice_to_xero` ran every Xero HTTP call inside the transaction that held the invoice row lock. A token refresh saved its rotated refresh token into a savepoint; the failed POST rolled it back. Xero had already consumed the old token.
- **Change:** `xero_integration/services.py` — network calls run outside any transaction; a short locked write of the sync record replaces the long lock. `xero_integration/client.py` — `XeroAuthError` is now a subclass of `XeroAPIError`, so transient token-endpoint failures reach the existing error handlers instead of crashing the nightly sweep. `xero_integration/tasks.py` — rate-limit budget is sweep-wide, honours `Retry-After`, and returns a partial summary on the soft time limit.
- **Test:** `xero_integration.test_push_resilience`

### 2. Document expiry reminders consumed with no email
- **Symptom:** A passport or insurance reminder never arrived and never retried.
- **Cause:** `send_document_expiry_reminders` set the sent flag, then called the sender with no `try/except`. Any exception left the flags set.
- **Change:** `notifications/tasks.py` — claim is rolled back on exception, matching the other reminder tasks. Also `send_overdue_invoice_reminders` now uses the local date, not UTC.
- **Test:** `notifications.test_reminders` (`DocumentExpiryClaimTests`)

### 3. Sign-in could 500 through analytics
- **Symptom:** A 500 on the login page when the PostHog client or the role lookup failed.
- **Cause:** `analytics.capture` promised never to raise but only guarded the final SDK call. It runs inside the `user_logged_in` receiver.
- **Change:** `core/analytics.py` — the whole capture path and the client constructor are guarded.
- **Test:** `core.tests.test_analytics`

### 4. Bulk departure with a future date removed horses immediately
- **Symptom:** Selecting horses on a location page with next week's date made them vanish from the field, capacity counts and the Current tab, and auto-rested the field.
- **Cause:** `PlacementService.bulk_depart` closed the placement whatever the date; the single-horse path already treated a future date as scheduled.
- **Change:** `core/services.py`, `core/views/locations.py` — future dates set `expected_departure` and the page says "scheduled". Non-numeric `horse_ids` are dropped before the query (was a 500) in the location, dashboard and bulk health endpoints. `horse_depart` tells the user when there is no placement instead of a silent redirect.
- **Test:** `core.tests.test_placement_lifecycle` (`FutureDepartureTests`)

### 5. Junk filter parameters returned 500
- **Symptom:** `?owner=abc`, `?location=abc`, `?horse=abc`, `?year=0`, and an unknown health `?type=` in an HTMX request all crashed.
- **Change:** `core/views/horses.py`, `core/views/placements.py`, `core/views/locations.py`, `health/views.py`, `invoicing/views.py`, `billing/views.py` — non-numeric ids are treated as unset, the year clamps, the tab falls back to overview.
- **Test:** `core.tests.test_bad_query_params`

### 6. Charges on a live invoice could be edited or deleted
- **Symptom:** Deleting a split charge already billed to one co-owner orphaned the invoice line, and that owner's invoice page and PDF then returned 500. Editing the amount left the splits not summing to the charge.
- **Cause:** The guards checked only `charge.invoiced`, which stays False for a split charge until every co-owner is billed. The sort in `group_line_items_by_horse` compared a date with an int for a line whose charge was gone. The guards also ran before the access check.
- **Change:** `billing/views.py` — refuse edit/delete when the charge sits on any non-cancelled invoice; access is checked first. `invoicing/utils.py` — type-stable sort key.
- **Test:** `invoicing.test_charge_invoice_integrity`

### 7. "Paid" on the invoice edit form did not record a payment
- **Symptom:** Status showed Paid, balance due showed the full total, the statement still listed it as owed, aged debtors dropped it, and there was no way back.
- **Change:** `invoicing/views.py` — the transition goes through `mark_as_paid`. Mark-as-paid and record-payment now take a row lock so a double-click cannot record two payments.
- **Test:** `invoicing.test_charge_invoice_integrity` (`PaidViaEditFormTests`)

### 8. Archived locations accepted by the arrival and move forms
- **Cause:** The forms replaced the rendered choices only; `ModelChoiceField` validates against `.queryset`, which was still all locations.
- **Change:** `core/forms.py` — `MoveHorseForm`, `SingleArrivalForm`, `NewArrivalForm` use `Location.objects.active()`.
- **Test:** `core.tests.test_form_guards`

### 9. Farrier date edit kept the old due date; zero-cost feed recharge
- **Change:** `health/forms.py` — editing the visit date without touching the due date clears it so `save()` recomputes. `billing/views.py` — a feed out with no cost is recorded unrecharged with a warning instead of creating a permanent £0.00 charge per horse.
- **Test:** `core.tests.test_form_guards`

### 10. Invoice emailed with no PDF and marked sent
- **Cause:** ReportLab parses text as markup; a `<` or `&` in an address, description or the notes raised. `send_invoice_email` swallowed it, sent without the attachment and returned True.
- **Change:** `invoicing/pdf.py` — all free text escaped. `notifications/emails.py` — a PDF failure means no email and a visible failure.
- **Test:** `invoicing.test_pdf_safety`

### 11. Dashboard departure buttons hid refusals
- **Cause:** The HTMX views answered an empty 200, which the row swap treated as "delete the row" even when the action was refused; the message only appeared on some later page.
- **Change:** `core/views/horses.py` — 204 + `HX-Trigger: popup:saved`, which refreshes the page content and shows the toast.
- **Test:** `core.tests.test_placement_lifecycle` (`HtmxDepartureResponseTests`)

### 12. Smaller fixes in the same commits
- `core/services.py` — `undo_auto_rest` opens a replacement usage period when the auto rest had no predecessor.
- `core/auth_backends.py` — no `LIMIT 2` on an unordered match query.
- `core/images.py` — uploads above 50 megapixels are always downscaled; JPEGs decode at reduced size.
- `templates/base.html` — bulk-action modal handles a failed fetch and stops stacking a document listener per visit.

## Hardening added

- **CI** (`.github/workflows/ci.yml`): ruff, missing-migration check, Django system checks, full test suite on every push and PR. The repo had no CI.
- **Lint** (`horse_management/ruff.toml`): error-only rules (undefined names, unused variables, bugbear). The seven pre-existing findings were dead code and are cleared.
- **Error pages** (`templates/404.html`, `templates/500.html`): branded pages instead of bare text with `DEBUG=False`.
- **Logging** (`settings.py`): a root logger with the console handler, so `logger.exception()` in any app reaches the host logs at INFO and above.
- **README**: test and lint commands.

## Proposed follow-ups (not done here)

1. ~~Queue bulk invoice sending.~~ Done: `invoice_bulk_action` now claims each draft (`Invoice.send_queued_at`) and dispatches `invoicing.tasks.send_invoice_email_task` per invoice. The list shows "Sending…" while queued and "Send failed" with the reason (`Invoice.send_error`) if the task could not deliver. `INVOICE_SEND_ASYNC` (default on, off on Vercel where there is no worker) switches back to inline sending. Tests: `invoicing.test_send_queue`.
2. **PostHog proxy `X-Forwarded-For`.** `_client_ip` takes the leftmost entry, which the client can set. Take the rightmost (added by the platform edge) or drop it. The existing test asserts the current behaviour, so this is a deliberate change to make.
3. **Vercel cold-start migrations.** `wsgi.py` runs `migrate` on every cold start on Vercel. Move it to a deploy step as Railway already does.
4. **Retire the root-level QA scripts** (`verify_*.py`, `mobile_*.py`, `seed_qa.py`) or move them under a `scripts/` folder so lint and coverage tooling can ignore them by path.
5. **Type checking.** Add `mypy` with `django-stubs` in permissive mode to CI and tighten module by module.
6. **Pre-commit hook** running `ruff check` so lint failures never reach CI.
