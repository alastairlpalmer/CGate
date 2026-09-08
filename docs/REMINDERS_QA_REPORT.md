# CGate — Reminder / Notification Logic QA

**Focus:** the automated email reminders (Celery tasks in `notifications/tasks.py`): which records they select, who they email, their date windows, and their throttling/dedup. This area was explicitly deferred in the original `QA_REPORT.md` ("with email stubbed, verify the reminders select the correct records… do not actually send") and had **no test coverage**.

**Method:** exercised each task directly with the **locmem email backend** (nothing sent) and asserted on `mail.outbox` recipients/subjects. Seeded boundary-date records relative to "today" (vaccinations at/before/after their reminder window, upcoming/overdue farrier visits, sent/overdue/paid/draft invoices, a confirmed pregnancy at the month-5 EHV window). Harness: `horse_management/verify_reminders.py`; regression tests: `horse_management/notifications/test_reminders.py` (19 tests).

---

## Verified correct (no defect)

- **Vaccination reminders** — fire once when `today ≥ next_due − reminder_days_before` (so both *due-soon* and *overdue* nudge), skip records before the window, exclude inactive horses, and the `reminder_sent` flag prevents repeats. Recipient = the horse's current owner.
- **Overdue-invoice reminders** — fire for `SENT`/`OVERDUE` invoices past their due date, throttle to at most once per 7 days (`last_overdue_reminder_at`), re-send after the window, and exclude `PAID`/`DRAFT` and not-yet-due invoices.
- **EHV reminders** — fire in the month-5/7/9 window (−14 to +7 days around each due date), record the month in `ehv_reminders_sent` so they don't repeat, and only for `confirmed` pregnancies on active mares.
- **`check_invoice_status`** — promotes `SENT` past-due invoices to `OVERDUE` and leaves `PAID` untouched.
- **No-email owners** — every task claims its row *then* rolls the claim back when the send fails (no recipient email), so the reminder isn't silently consumed and will go out once an email address is added. Verified for vaccination (`reminder_sent`) and overdue invoice (`last_overdue_reminder_at`).

---

## Fixed

### Overdue farrier visits never got a reminder — Medium
**Where:** `notifications/tasks.py::send_farrier_reminders`.
**Cause:** the query filtered `next_due_date__gte=today` (due within the next 14 days *only*), so a farrier visit whose due date had already passed was never selected — no reminder was ever sent for an **overdue** farrier. This is inconsistent with vaccination reminders, which *do* fire when overdue, and it drops the nudge exactly when it matters most (the horse has slipped past its farrier date). It also computed "latest visit per horse" over the *filtered* set, which — once overdue visits were included — could fire a reminder for a stale, already-superseded older visit.
**Evidence:** `verify_reminders.py` — before the fix only the upcoming "FarDue" fired (1 reminder); the overdue "FarOverdue" was silently skipped.
**Fix:** select each active horse's **latest** farrier visit across *all* its visits, then remind when that visit is unsent and `next_due_date ≤ today + 14 days` (i.e. due-soon **or** overdue). Using the latest visit prevents a superseded older visit from triggering a reminder; including overdue matches vaccination behaviour. One reminder per visit (the `reminder_sent` flag still prevents repeats).
**Verified:** both the upcoming and the overdue farrier now fire (2 reminders); a horse re-shod recently (latest visit due in the future) is *not* reminded because of an older overdue visit. Covered by `FarrierReminderTests::{test_overdue_fires, test_superseded_older_visit_does_not_fire}`.

---

## Coverage & notes

- Added **19 regression tests** (`notifications/test_reminders.py`) across all five tasks — selection, recipients, windows, throttling, dedup, inactive/paid exclusions, and no-email rollback. Full suite green (**134 passed**, up from 115).
- **Not exercised:** real SMTP delivery and Celery Beat scheduling (out of scope / safety — tasks were called directly with a stubbed backend); the email **template** rendering was exercised implicitly (locmem still renders the body) but not visually reviewed.
- **Design note (not changed):** like vaccinations, the farrier and EHV reminders are *one-shot* (a single nudge per record via `reminder_sent` / `ehv_reminders_sent`), whereas overdue-invoice reminders repeat weekly. That's an intentional difference; flagged only so it's a conscious choice.
