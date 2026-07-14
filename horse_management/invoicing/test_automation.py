"""Tests for the automation batch: monthly draft generation, nightly Xero
payment sync, and the bulk push-to-Xero action."""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import BusinessSettings, Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin
from invoicing.models import Invoice
from invoicing.services import InvoiceService
from invoicing.tasks import generate_monthly_draft_invoices
from xero_integration.models import XeroInvoiceSync
from xero_integration.tasks import sync_xero_invoice_statuses


def _placed_owner(name="Alice", email="a@example.com", horse_name="Ghost"):
    owner = Owner.objects.create(name=name, email=email)
    loc = Location.objects.create(site="Colgate", name=f"Field-{horse_name}")
    rate = RateType.objects.create(name=f"Rate-{horse_name}", daily_rate=Decimal("5.00"))
    horse = Horse.objects.create(name=horse_name)
    Placement.objects.create(
        horse=horse, owner=owner, location=loc,
        rate_type=rate, start_date=timezone.now().date() - timedelta(days=90),
    )
    return owner


def _connected(is_connected=True):
    conn = MagicMock()
    conn.is_connected = is_connected
    return patch(
        'xero_integration.models.XeroConnection.get_connection',
        return_value=conn,
    )


class MonthlyGenerationTaskTests(TestCase):

    def setUp(self):
        self.owner = _placed_owner()

    def _last_month(self):
        return timezone.now().date().replace(day=1) - timedelta(days=1)

    def test_generates_drafts_for_previous_month(self):
        result = generate_monthly_draft_invoices()
        self.assertIn("Generated 1", result)
        invoice = Invoice.objects.get()
        last_month = self._last_month()
        self.assertEqual(invoice.status, Invoice.Status.DRAFT)
        self.assertEqual(invoice.period_start, last_month.replace(day=1))
        self.assertEqual(invoice.period_end, last_month)

    def test_second_run_is_duplicate_safe(self):
        generate_monthly_draft_invoices()
        result = generate_monthly_draft_invoices()
        self.assertEqual(Invoice.objects.count(), 1)
        self.assertIn("skipped 1", result)

    def test_respects_settings_toggle(self):
        settings_obj = BusinessSettings.get_settings()
        settings_obj.auto_generate_invoices = False
        settings_obj.save()
        result = generate_monthly_draft_invoices()
        self.assertEqual(result, "disabled")
        self.assertEqual(Invoice.objects.count(), 0)


class XeroSyncTaskTests(TestCase):

    def setUp(self):
        self.owner = _placed_owner()
        last_month_end = timezone.now().date().replace(day=1) - timedelta(days=1)
        self.invoice = InvoiceService.create_invoice(
            self.owner, last_month_end.replace(day=1), last_month_end
        )
        self.invoice.mark_as_sent()
        self.sync = XeroInvoiceSync.objects.create(
            invoice=self.invoice,
            xero_invoice_id='xero-123',
            sync_status=XeroInvoiceSync.SyncStatus.PUSHED,
        )

    def test_marks_invoice_paid_when_xero_reports_paid(self):
        client = MagicMock()
        client.get_invoice.return_value = {'Status': 'PAID'}
        with _connected(), patch(
            'xero_integration.services.XeroClient', return_value=client
        ):
            result = sync_xero_invoice_statuses()

        self.assertIn("1 newly paid", result)
        self.invoice.refresh_from_db()
        self.sync.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.Status.PAID)
        self.assertEqual(self.sync.sync_status, XeroInvoiceSync.SyncStatus.PAID_IN_XERO)
        # The payment ledger must record how the invoice was settled.
        payment = self.invoice.payments.get()
        self.assertEqual(payment.method, 'xero')
        self.assertEqual(payment.amount, self.invoice.total)

    def test_unpaid_invoice_left_alone(self):
        client = MagicMock()
        client.get_invoice.return_value = {'Status': 'AUTHORISED'}
        with _connected(), patch(
            'xero_integration.services.XeroClient', return_value=client
        ):
            result = sync_xero_invoice_statuses()
        self.assertIn("0 newly paid", result)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.Status.SENT)

    def test_not_connected_skips_sweep(self):
        with _connected(is_connected=False):
            result = sync_xero_invoice_statuses()
        self.assertEqual(result, "not_connected")

    def test_per_invoice_error_does_not_block_others(self):
        from xero_integration.client import XeroAPIError

        owner2 = _placed_owner("Bob", "b@example.com", "Thunder")
        last_month_end = timezone.now().date().replace(day=1) - timedelta(days=1)
        invoice2 = InvoiceService.create_invoice(
            owner2, last_month_end.replace(day=1), last_month_end
        )
        invoice2.mark_as_sent()
        XeroInvoiceSync.objects.create(
            invoice=invoice2,
            xero_invoice_id='xero-456',
            sync_status=XeroInvoiceSync.SyncStatus.PUSHED,
        )

        client = MagicMock()
        client.get_invoice.side_effect = [
            XeroAPIError("rate limited"),
            {'Status': 'PAID'},
        ]
        with _connected(), patch(
            'xero_integration.services.XeroClient', return_value=client
        ):
            result = sync_xero_invoice_statuses()
        self.assertIn("1 newly paid", result)
        self.assertIn("1 error", result)


class BulkPushToXeroTests(TestCase):

    def setUp(self):
        self.staff = make_admin()
        self.owner = _placed_owner()
        last_month_end = timezone.now().date().replace(day=1) - timedelta(days=1)
        self.invoice = InvoiceService.create_invoice(
            self.owner, last_month_end.replace(day=1), last_month_end
        )
        self.client.force_login(self.staff)

    def _post(self, ids):
        return self.client.post(
            reverse("invoice_bulk_action"),
            {"action": "push_xero", "invoice_ids": ids},
            follow=True,
        )

    def test_pushes_selected_invoices(self):
        with _connected(), patch(
            'xero_integration.services.push_invoice_to_xero'
        ) as push:
            response = self._post([self.invoice.pk])
        push.assert_called_once_with(self.invoice)
        self.assertContains(response, "Pushed 1 invoice")

    def test_already_pushed_skipped(self):
        XeroInvoiceSync.objects.create(
            invoice=self.invoice,
            xero_invoice_id='xero-123',
            sync_status=XeroInvoiceSync.SyncStatus.PUSHED,
        )
        with _connected(), patch(
            'xero_integration.services.push_invoice_to_xero'
        ) as push:
            response = self._post([self.invoice.pk])
        push.assert_not_called()
        self.assertContains(response, "Skipped 1 invoice")

    def test_cancelled_skipped(self):
        self.invoice.status = Invoice.Status.CANCELLED
        self.invoice.save(update_fields=["status"])
        with _connected(), patch(
            'xero_integration.services.push_invoice_to_xero'
        ) as push:
            self._post([self.invoice.pk])
        push.assert_not_called()

    def test_not_connected_errors_out(self):
        with _connected(is_connected=False), patch(
            'xero_integration.services.push_invoice_to_xero'
        ) as push:
            response = self._post([self.invoice.pk])
        push.assert_not_called()
        self.assertContains(response, "Xero is not connected")
