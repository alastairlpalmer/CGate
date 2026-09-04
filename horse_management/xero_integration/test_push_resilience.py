"""Regression tests for the Xero push/sweep error paths.

Two bugs lived here:

* A token refresh triggered mid-push was saved inside the invoice
  transaction. When the invoice POST then failed, the rollback threw away
  the rotated refresh token that Xero had already consumed, and the
  integration was dead until someone reconnected.
* ``XeroAuthError`` (transient token-endpoint failure) was a sibling of
  ``XeroAPIError``, so it escaped every ``except XeroAPIError`` — the
  nightly sweep crashed mid-run and a failed push left no sync record.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from core.models import Horse, Location, Owner, Placement, RateType
from invoicing.services import InvoiceService
from xero_integration.client import XeroAPIError, XeroAuthError
from xero_integration.models import XeroConnection, XeroContactMapping, XeroInvoiceSync
from xero_integration.services import push_invoice_to_xero
from xero_integration.tasks import sync_xero_invoice_statuses


def _invoice(name="Alice", horse_name="Ghost"):
    owner = Owner.objects.create(name=name, email=f"{name.lower()}@example.com")
    loc = Location.objects.create(site="Colgate", name=f"Field-{horse_name}")
    rate = RateType.objects.create(name=f"Rate-{horse_name}", daily_rate=Decimal("5.00"))
    horse = Horse.objects.create(name=horse_name)
    Placement.objects.create(
        horse=horse, owner=owner, location=loc, rate_type=rate,
        start_date=timezone.localdate() - timedelta(days=90),
    )
    end = timezone.localdate().replace(day=1) - timedelta(days=1)
    invoice = InvoiceService.create_invoice(owner, end.replace(day=1), end)
    XeroContactMapping.objects.create(
        owner=owner, xero_contact_id=f"contact-{name}", xero_contact_name=name,
    )
    return invoice


def _real_connection():
    conn = XeroConnection.get_connection()
    conn.is_active = True
    conn.xero_tenant_id = "tenant"
    conn.access_token = "old-access"
    conn.refresh_token = "old-refresh"
    conn.token_expires_at = timezone.now() + timedelta(hours=1)
    conn.connected_at = timezone.now()
    conn.last_refreshed_at = timezone.now()
    conn.save()
    return conn


class PushErrorHierarchyTests(TestCase):

    def test_auth_error_is_an_api_error(self):
        self.assertTrue(issubclass(XeroAuthError, XeroAPIError))

    def test_transient_auth_failure_records_sync_error(self):
        invoice = _invoice()
        _real_connection()
        client = MagicMock()
        client.create_invoice.side_effect = XeroAuthError("token endpoint 503")
        with patch("xero_integration.services.XeroClient", return_value=client):
            with self.assertRaises(XeroAuthError):
                push_invoice_to_xero(invoice)
        sync = XeroInvoiceSync.objects.get(invoice=invoice)
        self.assertEqual(sync.sync_status, XeroInvoiceSync.SyncStatus.ERROR)
        self.assertIn("503", sync.error_message)

    def test_failed_push_keeps_rotated_refresh_token(self):
        invoice = _invoice()
        conn = _real_connection()

        def refresh_then_fail(*_args, **_kwargs):
            # Simulate _refresh_access_token persisting a rotated token
            # immediately before the invoice POST fails.
            XeroConnection.objects.filter(pk=conn.pk).update(
                access_token="new-access", refresh_token="new-refresh",
            )
            raise XeroAPIError("Validation error", status_code=400)

        client = MagicMock()
        client.create_invoice.side_effect = refresh_then_fail
        with patch("xero_integration.services.XeroClient", return_value=client):
            with self.assertRaises(XeroAPIError):
                push_invoice_to_xero(invoice)

        conn.refresh_from_db()
        self.assertEqual(conn.refresh_token, "new-refresh")
        self.assertEqual(
            XeroInvoiceSync.objects.get(invoice=invoice).sync_status,
            XeroInvoiceSync.SyncStatus.ERROR,
        )

    def test_successful_push_records_pushed(self):
        invoice = _invoice()
        _real_connection()
        client = MagicMock()
        client.create_invoice.return_value = {"InvoiceID": "x-1", "InvoiceNumber": "INV-1"}
        with patch("xero_integration.services.XeroClient", return_value=client):
            sync = push_invoice_to_xero(invoice)
        self.assertEqual(sync.sync_status, XeroInvoiceSync.SyncStatus.PUSHED)
        self.assertEqual(sync.xero_invoice_id, "x-1")
        # A repeat push is a no-op — no second POST.
        with patch("xero_integration.services.XeroClient", return_value=client):
            push_invoice_to_xero(invoice)
        self.assertEqual(client.create_invoice.call_count, 1)

    def test_error_then_success_clears_error_record(self):
        invoice = _invoice()
        _real_connection()
        client = MagicMock()
        client.create_invoice.side_effect = [
            XeroAPIError("boom"),
            {"InvoiceID": "x-2", "InvoiceNumber": "INV-2"},
        ]
        with patch("xero_integration.services.XeroClient", return_value=client):
            with self.assertRaises(XeroAPIError):
                push_invoice_to_xero(invoice)
            sync = push_invoice_to_xero(invoice)
        self.assertEqual(sync.sync_status, XeroInvoiceSync.SyncStatus.PUSHED)
        self.assertEqual(sync.error_message, "")
        self.assertEqual(XeroInvoiceSync.objects.filter(invoice=invoice).count(), 1)


class SweepAuthErrorTests(TestCase):

    def test_transient_auth_error_does_not_abort_sweep(self):
        inv1 = _invoice("Alice", "Ghost")
        inv2 = _invoice("Bob", "Thunder")
        for inv in (inv1, inv2):
            inv.mark_as_sent()
            XeroInvoiceSync.objects.create(
                invoice=inv, xero_invoice_id=f"x-{inv.pk}",
                sync_status=XeroInvoiceSync.SyncStatus.PUSHED,
            )
        _real_connection()
        client = MagicMock()
        client.get_invoice.side_effect = [
            XeroAuthError("token endpoint 503"),
            {"Status": "PAID"},
        ]
        with patch("xero_integration.services.XeroClient", return_value=client), \
                patch("xero_integration.tasks.time.sleep"):
            result = sync_xero_invoice_statuses()
        self.assertIn("1 newly paid", result)
        self.assertIn("1 error", result)

    def test_rate_limit_budget_is_sweep_wide(self):
        invoices = [_invoice(n, f"H{n}") for n in ("A", "B", "C", "D", "E")]
        for inv in invoices:
            inv.mark_as_sent()
            XeroInvoiceSync.objects.create(
                invoice=inv, xero_invoice_id=f"x-{inv.pk}",
                sync_status=XeroInvoiceSync.SyncStatus.PUSHED,
            )
        _real_connection()
        client = MagicMock()
        client.get_invoice.side_effect = XeroAPIError(
            "rate limited", status_code=429, retry_after=5,
        )
        with patch("xero_integration.services.XeroClient", return_value=client), \
                patch("xero_integration.tasks.time.sleep") as sleep:
            result = sync_xero_invoice_statuses()
        self.assertIn("5 error", result)
        waits = [c.args[0] for c in sleep.call_args_list if c.args[0] == 5]
        # Three retries total across the sweep, not three per invoice.
        self.assertEqual(len(waits), 3)
