"""Regression tests for the reminder Celery tasks.

The Django test runner swaps in the locmem email backend, so we assert on
`mail.outbox` (recipients/subjects) without sending real mail.

Covers record selection, recipients, date windows, throttling/dedup and the
no-email rollback for each task — none of which was previously tested.
"""

from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.test import TestCase
from django.utils import timezone

from core.models import Horse, Owner, OwnershipShare
from health.models import (
    BreedingRecord,
    FarrierVisit,
    Vaccination,
    VaccinationType,
)
from invoicing.models import Invoice
from notifications.tasks import (
    check_invoice_status,
    send_ehv_reminders,
    send_farrier_reminders,
    send_overdue_invoice_reminders,
    send_vaccination_reminders,
)

TODAY = timezone.now().date()


def _owner(name, email="owner@example.com"):
    return Owner.objects.create(name=name, email=email)


def _horse(name, owner, active=True):
    h = Horse.objects.create(name=name, is_active=active)
    OwnershipShare.objects.create(
        horse=h, owner=owner, share_percentage=Decimal("100"), is_primary_contact=True
    )
    return h


def _recipients():
    return sorted(addr for m in mail.outbox for addr in m.to)


class VaccinationReminderTests(TestCase):
    def setUp(self):
        self.vt = VaccinationType.objects.create(
            name="Flu", interval_months=12, reminder_days_before=30
        )

    def _vax(self, owner_name, due_offset, active=True, email="o@example.com"):
        o = _owner(owner_name, email)
        h = _horse(owner_name + "H", o, active=active)
        return Vaccination.objects.create(
            horse=h, vaccination_type=self.vt,
            date_given=TODAY - timedelta(days=300),
            next_due_date=TODAY + timedelta(days=due_offset),
        )

    def test_due_within_reminder_window_fires(self):
        self._vax("Due", due_offset=20)  # reminder_date = due-30 = 10d ago
        send_vaccination_reminders()
        self.assertEqual(len(mail.outbox), 1)

    def test_before_reminder_window_does_not_fire(self):
        self._vax("Early", due_offset=40)  # reminder_date = +10d, not yet
        send_vaccination_reminders()
        self.assertEqual(len(mail.outbox), 0)

    def test_overdue_still_fires(self):
        self._vax("Overdue", due_offset=-5)
        send_vaccination_reminders()
        self.assertEqual(len(mail.outbox), 1)

    def test_inactive_horse_excluded(self):
        self._vax("Inactive", due_offset=5, active=False)
        send_vaccination_reminders()
        self.assertEqual(len(mail.outbox), 0)

    def test_reminder_sent_flag_prevents_repeat(self):
        self._vax("Due", due_offset=10)
        send_vaccination_reminders()
        send_vaccination_reminders()
        self.assertEqual(len(mail.outbox), 1)

    def test_no_email_owner_rolls_back_claim(self):
        v = self._vax("NoEmail", due_offset=10, email="")
        send_vaccination_reminders()
        self.assertEqual(len(mail.outbox), 0)
        v.refresh_from_db()
        self.assertFalse(v.reminder_sent)  # not consumed; retried once email added


class FarrierReminderTests(TestCase):
    def _visit(self, owner_name, due_offset, visit_days_ago=30, email="o@example.com"):
        o = _owner(owner_name, email)
        h = _horse(owner_name + "H", o)
        return o, h, FarrierVisit.objects.create(
            horse=h, date=TODAY - timedelta(days=visit_days_ago),
            work_done="full_set", next_due_date=TODAY + timedelta(days=due_offset),
        )

    def test_due_within_two_weeks_fires(self):
        self._visit("Due", due_offset=10)
        send_farrier_reminders()
        self.assertEqual(len(mail.outbox), 1)

    def test_beyond_two_weeks_does_not_fire(self):
        self._visit("Future", due_offset=20)
        send_farrier_reminders()
        self.assertEqual(len(mail.outbox), 0)

    def test_overdue_fires(self):
        # Regression: overdue farrier visits previously got no reminder.
        self._visit("Overdue", due_offset=-5, visit_days_ago=50)
        send_farrier_reminders()
        self.assertEqual(len(mail.outbox), 1)

    def test_superseded_older_visit_does_not_fire(self):
        # Horse re-shod recently (latest visit due in the future) must not be
        # reminded because of an older, overdue, unsent visit.
        o = _owner("Reshod")
        h = _horse("ReshodH", o)
        FarrierVisit.objects.create(  # old, overdue, unsent
            horse=h, date=TODAY - timedelta(days=90), work_done="full_set",
            next_due_date=TODAY - timedelta(days=6),
        )
        FarrierVisit.objects.create(  # latest, not due yet
            horse=h, date=TODAY - timedelta(days=3), work_done="full_set",
            next_due_date=TODAY + timedelta(days=39),
        )
        send_farrier_reminders()
        self.assertEqual(len(mail.outbox), 0)

    def test_flag_prevents_repeat(self):
        self._visit("Due", due_offset=10)
        send_farrier_reminders()
        send_farrier_reminders()
        self.assertEqual(len(mail.outbox), 1)


class OverdueInvoiceReminderTests(TestCase):
    def _invoice(self, owner_name, status, due_offset=-10, email="o@example.com"):
        o = _owner(owner_name, email)
        return Invoice.objects.create(
            owner=o, invoice_number=f"T-{owner_name}",
            period_start=TODAY - timedelta(days=60),
            period_end=TODAY - timedelta(days=40),
            due_date=TODAY + timedelta(days=due_offset),
            status=status, total=Decimal("100"),
        )

    def test_sent_past_due_fires_once_then_throttled(self):
        self._invoice("Sent", Invoice.Status.SENT)
        send_overdue_invoice_reminders()
        self.assertEqual(len(mail.outbox), 1)
        mail.outbox.clear()
        send_overdue_invoice_reminders()  # same day -> throttled
        self.assertEqual(len(mail.outbox), 0)

    def test_resends_after_repeat_window(self):
        inv = self._invoice("Sent", Invoice.Status.OVERDUE)
        send_overdue_invoice_reminders()
        Invoice.objects.filter(pk=inv.pk).update(
            last_overdue_reminder_at=timezone.now() - timedelta(days=8)
        )
        mail.outbox.clear()
        send_overdue_invoice_reminders()
        self.assertEqual(len(mail.outbox), 1)

    def test_not_yet_due_excluded(self):
        self._invoice("Future", Invoice.Status.SENT, due_offset=10)
        send_overdue_invoice_reminders()
        self.assertEqual(len(mail.outbox), 0)

    def test_paid_and_draft_excluded(self):
        self._invoice("Paid", Invoice.Status.PAID)
        self._invoice("Draft", Invoice.Status.DRAFT)
        send_overdue_invoice_reminders()
        self.assertEqual(len(mail.outbox), 0)

    def test_no_email_owner_rolls_back_timestamp(self):
        inv = self._invoice("NoEmail", Invoice.Status.OVERDUE, email="")
        send_overdue_invoice_reminders()
        self.assertEqual(len(mail.outbox), 0)
        inv.refresh_from_db()
        self.assertIsNone(inv.last_overdue_reminder_at)


class EhvReminderTests(TestCase):
    def _mare(self, name, email="o@example.com"):
        o = _owner(name, email)
        m = Horse.objects.create(name=name, sex="mare", is_active=True)
        OwnershipShare.objects.create(horse=m, owner=o, share_percentage=Decimal("100"), is_primary_contact=True)
        return m

    def test_month5_window_fires_then_dedups(self):
        m = self._mare("Mare")
        # covering ~5 months ago so month-5 EHV due is around today
        BreedingRecord.objects.create(
            mare=m, stallion_name="S",
            date_covered=TODAY - timedelta(days=150), status="confirmed",
        )
        send_ehv_reminders()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Month 5", mail.outbox[0].subject)
        mail.outbox.clear()
        send_ehv_reminders()  # already recorded in ehv_reminders_sent
        self.assertEqual(len(mail.outbox), 0)

    def test_unconfirmed_record_excluded(self):
        m = self._mare("Mare2")
        BreedingRecord.objects.create(
            mare=m, stallion_name="S",
            date_covered=TODAY - timedelta(days=150), status="covered",
        )
        send_ehv_reminders()
        self.assertEqual(len(mail.outbox), 0)


class CheckInvoiceStatusTests(TestCase):
    def test_sent_past_due_promoted_to_overdue(self):
        o = _owner("O")
        inv = Invoice.objects.create(
            owner=o, invoice_number="S1",
            period_start=TODAY - timedelta(days=60), period_end=TODAY - timedelta(days=40),
            due_date=TODAY - timedelta(days=1), status=Invoice.Status.SENT, total=Decimal("10"),
        )
        paid = Invoice.objects.create(
            owner=o, invoice_number="P1",
            period_start=TODAY - timedelta(days=60), period_end=TODAY - timedelta(days=40),
            due_date=TODAY - timedelta(days=1), status=Invoice.Status.PAID, total=Decimal("10"),
        )
        check_invoice_status()
        inv.refresh_from_db(); paid.refresh_from_db()
        self.assertEqual(inv.status, Invoice.Status.OVERDUE)
        self.assertEqual(paid.status, Invoice.Status.PAID)
