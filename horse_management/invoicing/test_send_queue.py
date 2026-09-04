"""Bulk invoice sending is queued through Celery, one task per invoice.

The request claims each draft (send_queued_at) and returns at once; the
task emails it and marks it sent. A failed send releases the claim and
records why, so the invoice stays in the draft queue with a visible reason
rather than silently vanishing or silently staying.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin
from invoicing.models import Invoice
from invoicing.services import InvoiceService
from invoicing.tasks import (
    SEND_CLAIM_TIMEOUT,
    claim_invoice_for_sending,
    send_invoice_email_task,
)


def _invoice(name="Alice", email="a@example.com", horse="Ghost"):
    owner = Owner.objects.create(name=name, email=email)
    loc = Location.objects.create(site="S", name=f"F-{horse}")
    rate = RateType.objects.create(name=f"R-{horse}", daily_rate=Decimal("5.00"))
    h = Horse.objects.create(name=horse)
    end = timezone.localdate().replace(day=1) - timedelta(days=1)
    Placement.objects.create(
        horse=h, owner=owner, location=loc, rate_type=rate,
        start_date=end.replace(day=1) - timedelta(days=10),
    )
    return InvoiceService.create_invoice(owner, end.replace(day=1), end)


class ClaimTests(TestCase):
    def test_claim_is_exclusive(self):
        inv = _invoice()
        self.assertTrue(claim_invoice_for_sending(inv.pk))
        self.assertFalse(claim_invoice_for_sending(inv.pk))
        inv.refresh_from_db()
        self.assertTrue(inv.is_send_queued)

    def test_stale_claim_can_be_retaken(self):
        inv = _invoice()
        Invoice.objects.filter(pk=inv.pk).update(
            send_queued_at=timezone.now() - SEND_CLAIM_TIMEOUT - timedelta(minutes=1)
        )
        self.assertTrue(claim_invoice_for_sending(inv.pk))

    def test_only_drafts_can_be_claimed(self):
        inv = _invoice()
        inv.mark_as_sent()
        self.assertFalse(claim_invoice_for_sending(inv.pk))


class TaskTests(TestCase):
    def test_sends_and_marks_sent(self):
        inv = _invoice()
        claim_invoice_for_sending(inv.pk)
        self.assertEqual(send_invoice_email_task(inv.pk), 'sent')
        inv.refresh_from_db()
        self.assertEqual(inv.status, Invoice.Status.SENT)
        self.assertIsNone(inv.send_queued_at)
        self.assertEqual(len(mail.outbox), 1)

    def test_unclaimed_invoice_is_skipped(self):
        inv = _invoice()
        self.assertEqual(send_invoice_email_task(inv.pk), 'skipped')
        self.assertEqual(len(mail.outbox), 0)

    def test_failure_releases_claim_and_records_reason(self):
        inv = _invoice()
        claim_invoice_for_sending(inv.pk)
        with patch("invoicing.pdf.generate_invoice_pdf", side_effect=RuntimeError("boom")):
            self.assertEqual(send_invoice_email_task(inv.pk), 'failed')
        inv.refresh_from_db()
        self.assertEqual(inv.status, Invoice.Status.DRAFT)
        self.assertIsNone(inv.send_queued_at)
        self.assertIn("Sending failed", inv.send_error)
        self.assertEqual(len(mail.outbox), 0)

    def test_exception_in_send_is_a_failure_not_a_crash(self):
        inv = _invoice()
        claim_invoice_for_sending(inv.pk)
        with patch("notifications.emails.send_invoice_email", side_effect=RuntimeError("smtp")):
            self.assertEqual(send_invoice_email_task(inv.pk), 'failed')
        inv.refresh_from_db()
        self.assertIsNone(inv.send_queued_at)

    def test_owner_without_email_is_skipped_with_reason(self):
        inv = _invoice(email="")
        claim_invoice_for_sending(inv.pk)
        self.assertEqual(send_invoice_email_task(inv.pk), 'skipped')
        inv.refresh_from_db()
        self.assertIn("no email", inv.send_error)
        self.assertIsNone(inv.send_queued_at)

    def test_missing_invoice_is_skipped(self):
        self.assertEqual(send_invoice_email_task(999999), 'skipped')


class BulkSendViewTests(TestCase):
    def setUp(self):
        self.client.force_login(make_admin())
        self.inv1 = _invoice("Alice", "a@example.com", "Ghost")
        self.inv2 = _invoice("Bob", "b@example.com", "Thunder")

    def _post(self, ids):
        return self.client.post(
            reverse("invoice_bulk_action"),
            {"action": "send", "invoice_ids": ids},
            follow=True,
        )

    def test_queues_one_task_per_invoice(self):
        with patch("invoicing.tasks.send_invoice_email_task.delay") as delay:
            resp = self._post([self.inv1.pk, self.inv2.pk])
        self.assertEqual(delay.call_count, 2)
        msgs = [str(m) for m in resp.context["messages"]]
        self.assertTrue(any("Sending 2 invoices in the background" in m for m in msgs), msgs)
        self.inv1.refresh_from_db()
        self.assertTrue(self.inv1.is_send_queued)
        self.assertEqual(self.inv1.status, Invoice.Status.DRAFT)

    def test_eager_worker_delivers_and_marks_sent(self):
        # test_settings runs Celery eagerly, so the whole path completes here.
        self._post([self.inv1.pk, self.inv2.pk])
        self.inv1.refresh_from_db()
        self.inv2.refresh_from_db()
        self.assertEqual(self.inv1.status, Invoice.Status.SENT)
        self.assertEqual(self.inv2.status, Invoice.Status.SENT)
        self.assertEqual(len(mail.outbox), 2)

    def test_double_submit_does_not_queue_twice(self):
        with patch("invoicing.tasks.send_invoice_email_task.delay") as delay:
            self._post([self.inv1.pk])
            resp = self._post([self.inv1.pk])
        self.assertEqual(delay.call_count, 1)
        msgs = [str(m) for m in resp.context["messages"]]
        self.assertTrue(any("Skipped 1 invoice" in m for m in msgs), msgs)

    @override_settings(INVOICE_SEND_ASYNC=False)
    def test_sync_fallback_sends_inline(self):
        with patch("invoicing.tasks.send_invoice_email_task.delay") as delay:
            resp = self._post([self.inv1.pk])
        delay.assert_not_called()
        msgs = [str(m) for m in resp.context["messages"]]
        self.assertTrue(any("Sent 1 invoice" in m for m in msgs), msgs)
        self.inv1.refresh_from_db()
        self.assertEqual(self.inv1.status, Invoice.Status.SENT)
        self.assertEqual(len(mail.outbox), 1)

    def test_list_shows_sending_badge(self):
        claim_invoice_for_sending(self.inv1.pk)
        resp = self.client.get(reverse("invoice_list") + "?status=draft")
        self.assertContains(resp, "Sending…")

    def test_detail_shows_send_error(self):
        Invoice.objects.filter(pk=self.inv1.pk).update(send_error="Sending failed — test")
        resp = self.client.get(reverse("invoice_detail", args=[self.inv1.pk]))
        self.assertContains(resp, "Send failed")
