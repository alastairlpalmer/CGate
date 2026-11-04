"""Invoicing defects from the November 2026 bug review that #216 left open.

2. A co-owner added after a period was billed was billed for it again.
5. One "Send" could email the owner twice.
6. Bulk "Mark paid" did not lock each invoice.
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, OwnershipShare, Placement, RateType
from core.roles_testutils import make_admin
from invoicing.models import Invoice, InvoiceLineItem
from invoicing.services import InvoiceService

JUNE = (date(2026, 6, 1), date(2026, 6, 30))
JULY = (date(2026, 7, 1), date(2026, 7, 31))


class CoOwnerAddedLaterTests(TestCase):
    """2. A share added in July must not bill June again."""

    def setUp(self):
        self.a = Owner.objects.create(name='Anna', email='anna@example.com')
        self.b = Owner.objects.create(name='Ben', email='ben@example.com')
        location = Location.objects.create(site='Colgate', name='Top Field')
        rate = RateType.objects.create(name='Grass', daily_rate=Decimal('10.00'))
        self.horse = Horse.objects.create(name='Ghost')
        Placement.objects.create(
            horse=self.horse, owner=self.a, location=location, rate_type=rate,
            start_date=date(2026, 5, 1),
        )
        self.share_a = OwnershipShare.objects.create(
            horse=self.horse, owner=self.a, share_percentage=Decimal('100'),
            is_primary_contact=True,
        )

    def _sell_half_to_b(self):
        self.share_a.share_percentage = Decimal('50')
        self.share_a.save()
        OwnershipShare.objects.create(
            horse=self.horse, owner=self.b, share_percentage=Decimal('50'),
        )

    def _livery_total(self, owner, period):
        lines = InvoiceService.calculate_livery_charges(owner, *period)
        return sum((line['amount'] for line in lines), Decimal('0'))

    def test_june_is_not_billed_again_to_the_new_co_owner(self):
        june = InvoiceService.create_invoice(self.a, *JUNE)
        self.assertEqual(june.subtotal, Decimal('300.00'))

        self._sell_half_to_b()

        self.assertEqual(InvoiceService.calculate_livery_charges(self.b, *JUNE), [])
        InvoiceService.generate_monthly_invoices(2026, 6)
        june_livery = InvoiceLineItem.objects.filter(
            horse=self.horse, line_type=InvoiceLineItem.LineType.LIVERY,
            invoice__period_start=JUNE[0],
        ).exclude(invoice__status=Invoice.Status.CANCELLED)
        self.assertEqual(
            sum(line.line_total for line in june_livery), Decimal('300.00'),
        )

    def test_july_is_split_between_both_owners(self):
        InvoiceService.create_invoice(self.a, *JUNE)
        self._sell_half_to_b()
        self.assertEqual(self._livery_total(self.a, JULY), Decimal('155.00'))
        self.assertEqual(self._livery_total(self.b, JULY), Decimal('155.00'))

    def test_a_cancelled_june_invoice_does_not_block_the_co_owner(self):
        june = InvoiceService.create_invoice(self.a, *JUNE)
        june.status = Invoice.Status.CANCELLED
        june.save(update_fields=['status'])
        self._sell_half_to_b()
        self.assertEqual(self._livery_total(self.b, JUNE), Decimal('150.00'))

    def test_only_the_unbilled_days_are_billed(self):
        InvoiceService.create_invoice(self.a, date(2026, 6, 1), date(2026, 6, 15))
        self._sell_half_to_b()
        lines = InvoiceService.calculate_livery_charges(self.b, *JUNE)
        self.assertEqual(sum(line['days'] for line in lines), 15)
        self.assertEqual(sum(line['amount'] for line in lines), Decimal('75.00'))


class SingleSendTests(TestCase):
    """5. The single "Send" claims the invoice, like the bulk send."""

    def setUp(self):
        self.client.force_login(make_admin())
        owner = Owner.objects.create(name='Anna', email='anna@example.com')
        self.invoice = Invoice.objects.create(
            owner=owner, period_start=JUNE[0], period_end=JUNE[1],
            due_date=JULY[0], total=Decimal('300.00'),
        )

    def _send(self):
        response = self.client.post(reverse('invoice_send', args=[self.invoice.pk]))
        return [str(m) for m in get_messages(response.wsgi_request)]

    @patch('notifications.emails.send_invoice_email', return_value=True)
    def test_a_double_click_sends_one_email(self, send):
        self._send()
        messages = self._send()
        self.assertEqual(send.call_count, 1)
        self.assertIn('less than a minute ago', messages[-1])
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.Status.SENT)

    @patch('notifications.emails.send_invoice_email', return_value=True)
    def test_no_send_while_a_send_is_queued(self, send):
        Invoice.objects.filter(pk=self.invoice.pk).update(send_queued_at=timezone.now())
        messages = self._send()
        send.assert_not_called()
        self.assertIn('already being sent', messages[0])

    @patch('notifications.emails.send_invoice_email', return_value=True)
    def test_a_sent_invoice_can_be_sent_again_later(self, send):
        Invoice.objects.filter(pk=self.invoice.pk).update(
            status=Invoice.Status.SENT, sent_at=timezone.now() - timedelta(hours=1),
        )
        self._send()
        send.assert_called_once()
        self.invoice.refresh_from_db()
        self.assertIsNone(self.invoice.send_queued_at)

    @patch('notifications.emails.send_invoice_email', return_value=False)
    def test_a_failed_send_frees_the_invoice(self, send):
        messages = self._send()
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.Status.DRAFT)
        self.assertIsNone(self.invoice.send_queued_at)
        self.assertTrue(messages)

    def test_the_list_hides_send_while_queued(self):
        Invoice.objects.filter(pk=self.invoice.pk).update(send_queued_at=timezone.now())
        response = self.client.get(reverse('invoice_list'))
        self.assertNotContains(response, reverse('invoice_send', args=[self.invoice.pk]))


class BulkMarkPaidTests(TestCase):
    """6. Bulk "Mark paid" reads each invoice again under a lock.

    The race itself needs two database connections, which SQLite tests
    cannot give; this checks the outcome of two submits in a row.
    """

    def test_two_submits_record_one_payment(self):
        self.client.force_login(make_admin())
        owner = Owner.objects.create(name='Anna')
        invoice = Invoice.objects.create(
            owner=owner, period_start=JUNE[0], period_end=JUNE[1],
            due_date=JULY[0], total=Decimal('300.00'), status=Invoice.Status.SENT,
        )
        for _ in range(2):
            self.client.post(reverse('invoice_bulk_action'), {
                'action': 'mark_paid', 'invoice_ids': [invoice.pk],
            })
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.assertEqual(invoice.payments.count(), 1)
