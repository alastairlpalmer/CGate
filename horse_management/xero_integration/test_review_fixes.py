"""The 5 Xero defects from the November 2026 bug review.

1. Bulk push ignored Xero's rate limit and ran inside the web request.
2. The Idempotency-Key never changed, so "push again" after Xero deleted
   the draft (or refused the push) replayed the old answer.
3. Connecting took the first organisation in Xero's list, not the one
   the user chose; disconnect left the app connected in Xero.
4. Contact links and push records were not tied to an organisation.
5. Two "paid in Xero" checks at once recorded the payment twice.
"""

import base64
import json
from unittest.mock import MagicMock, patch

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from core.roles_testutils import make_admin
from invoicing.models import Invoice
from xero_integration.client import XeroAPIError, XeroTokenExpiredError, choose_tenant
from xero_integration.models import XeroConnection, XeroContactMapping, XeroInvoiceSync
from xero_integration.services import (
    XeroContactService,
    check_xero_invoice_status,
    push_invoice_to_xero,
)
from xero_integration.tasks import push_invoices_to_xero, sync_xero_invoice_statuses
from xero_integration.test_push_resilience import _invoice, _real_connection


def _token(event_id):
    claims = base64.urlsafe_b64encode(
        json.dumps({'authentication_event_id': event_id}).encode()
    ).decode().rstrip('=')
    return f'header.{claims}.signature'


def _pushing_client(invoice_id='xero-inv-1'):
    client = MagicMock()
    client.create_invoice.return_value = {'InvoiceID': invoice_id, 'InvoiceNumber': 'INV-1'}
    return client


class BulkPushTaskTests(TestCase):
    """1. The bulk push waits between calls and waits out a 429."""

    def setUp(self):
        _real_connection()
        self.first = _invoice('Alice', 'Ghost')
        self.second = _invoice('Bea', 'Shadow')

    @patch('xero_integration.tasks.time.sleep')
    def test_a_rate_limit_waits_and_tries_the_same_invoice_again(self, sleep):
        calls = []

        def push(invoice):
            calls.append(invoice.pk)
            if len(calls) == 1:
                raise XeroAPIError('429', status_code=429, retry_after=7)

        with patch('xero_integration.services.push_invoice_to_xero', side_effect=push):
            summary = push_invoices_to_xero([self.first.pk, self.second.pk])

        self.assertEqual(calls, [self.first.pk, self.first.pk, self.second.pk])
        sleep.assert_called_once_with(7)
        self.assertEqual(summary, 'Pushed 2 invoice(s) to Xero, 0 failed.')

    def test_an_expired_connection_stops_the_run(self):
        with patch(
            'xero_integration.services.push_invoice_to_xero',
            side_effect=XeroTokenExpiredError('reconnect'),
        ) as push:
            summary = push_invoices_to_xero([self.first.pk, self.second.pk])
        self.assertEqual(push.call_count, 1)
        self.assertTrue(summary.startswith('stopped'))

    @patch('xero_integration.client.time.sleep')
    def test_calls_inside_the_run_are_spaced_out(self, sleep):
        from xero_integration.client import _wait_for_pace, paced_calls

        with paced_calls(1.1):
            _wait_for_pace()
            _wait_for_pace()
        sleep.assert_called_once()
        self.assertGreater(sleep.call_args[0][0], 1.0)
        # Outside a paced run, calls do not wait.
        _wait_for_pace()
        sleep.assert_called_once()


class IdempotencyKeyTests(TestCase):
    """2. A new key after Xero's final answer; the same key otherwise."""

    def setUp(self):
        _real_connection()
        self.invoice = _invoice()

    def _push(self, client):
        with patch('xero_integration.services.XeroClient', return_value=client):
            return push_invoice_to_xero(self.invoice)

    def _key(self, client):
        return client.create_invoice.call_args.kwargs['idempotency_key']

    def test_push_again_after_the_draft_was_deleted_in_xero_uses_a_new_key(self):
        first = _pushing_client()
        sync = self._push(first)
        self.assertEqual(self._key(first), f'yardway-invoice-{self.invoice.pk}')

        checker = MagicMock()
        checker.get_invoice.return_value = {'Status': 'DELETED'}
        with patch('xero_integration.services.XeroClient', return_value=checker):
            check_xero_invoice_status(sync)

        second = _pushing_client('xero-inv-2')
        sync = self._push(second)
        self.assertEqual(self._key(second), f'yardway-invoice-{self.invoice.pk}-1')
        self.assertEqual(sync.xero_invoice_id, 'xero-inv-2')

    def test_a_refused_push_gets_a_new_key_next_time(self):
        refused = MagicMock()
        refused.create_invoice.side_effect = XeroAPIError('400', status_code=400)
        with self.assertRaises(XeroAPIError):
            self._push(refused)
        retry = _pushing_client()
        self._push(retry)
        self.assertEqual(self._key(retry), f'yardway-invoice-{self.invoice.pk}-1')

    def test_a_rate_limit_or_network_error_keeps_the_key(self):
        # Xero may have accepted the invoice before a network error: the
        # same key makes the retry return that invoice, not a duplicate.
        for error in (XeroAPIError('429', status_code=429), XeroAPIError('network')):
            failing = MagicMock()
            failing.create_invoice.side_effect = error
            with self.assertRaises(XeroAPIError):
                self._push(failing)
        retry = _pushing_client()
        self._push(retry)
        self.assertEqual(self._key(retry), f'yardway-invoice-{self.invoice.pk}')


class ChooseTenantTests(TestCase):
    """3. The organisation from this authorisation, not the first one."""

    TENANTS = [
        {'tenantId': 'org-a', 'tenantName': 'A', 'authEventId': 'old-event'},
        {'tenantId': 'org-b', 'tenantName': 'B', 'authEventId': 'new-event'},
    ]

    def test_the_organisation_from_this_authorisation_is_chosen(self):
        self.assertEqual(choose_tenant(_token('new-event'), self.TENANTS)['tenantId'], 'org-b')

    def test_a_single_organisation_is_chosen(self):
        self.assertEqual(choose_tenant('not-a-jwt', self.TENANTS[:1])['tenantId'], 'org-a')

    def test_no_choice_when_it_is_not_clear(self):
        self.assertIsNone(choose_tenant(_token('unknown'), self.TENANTS))
        self.assertIsNone(choose_tenant('not-a-jwt', self.TENANTS))


class ConnectAndDisconnectTests(TestCase):
    """3 and 4. The callback, organisation changes, and disconnect."""

    def setUp(self):
        self.client.force_login(make_admin())
        conn = _real_connection()
        conn.xero_tenant_id = 'org-a'
        conn.oauth_state = 'state'
        conn.save()

    def _callback(self, event_id, tenants):
        tokens = {
            'access_token': _token(event_id), 'refresh_token': 'r', 'expires_in': 1800,
        }
        with patch(
            'xero_integration.views.XeroClient.exchange_code_for_tokens', return_value=tokens,
        ), patch(
            'xero_integration.views.XeroClient.get_tenant_connections', return_value=tenants,
        ):
            response = self.client.get(
                reverse('xero_callback'), {'code': 'c', 'state': 'state'},
            )
        return [str(m) for m in get_messages(response.wsgi_request)]

    def test_an_unclear_choice_does_not_connect(self):
        conn = XeroConnection.get_connection()
        conn.is_active = False
        conn.save()
        messages = self._callback('unknown', ChooseTenantTests.TENANTS)
        self.assertIn('More than one Xero organisation', messages[0])
        self.assertFalse(XeroConnection.get_connection().is_active)

    def test_a_different_organisation_clears_links_to_the_old_one(self):
        invoice = _invoice()  # Its contact mapping has no organisation (legacy).
        old_mapping = XeroContactMapping.objects.get(owner=invoice.owner)
        old_mapping.xero_tenant_id = 'org-a'
        old_mapping.save()
        XeroInvoiceSync.objects.create(
            invoice=invoice, xero_invoice_id='a-1', xero_invoice_number='INV-1',
            sync_status=XeroInvoiceSync.SyncStatus.PUSHED, xero_tenant_id='org-a',
        )

        messages = self._callback('new-event', ChooseTenantTests.TENANTS)

        self.assertEqual(XeroConnection.get_connection().xero_tenant_id, 'org-b')
        self.assertIn('1 contact and 1 invoice', messages[1])
        self.assertFalse(XeroContactMapping.objects.exists())
        sync = XeroInvoiceSync.objects.get(invoice=invoice)
        self.assertEqual(sync.sync_status, XeroInvoiceSync.SyncStatus.NOT_PUSHED)
        self.assertIn('INV-1', sync.error_message)
        self.assertEqual(sync.push_round, 1)
        # Still recorded as having been in Xero, so it cannot be deleted.
        self.assertTrue(invoice.deletion_blocker)

    def test_reconnecting_the_same_organisation_keeps_the_links(self):
        invoice = _invoice()
        XeroContactMapping.objects.filter(owner=invoice.owner).update(xero_tenant_id='org-a')
        messages = self._callback('old-event', ChooseTenantTests.TENANTS)
        self.assertEqual(len(messages), 1)
        self.assertTrue(XeroContactMapping.objects.exists())

    def test_disconnect_removes_the_access_in_xero(self):
        with patch(
            'xero_integration.views.XeroClient.revoke_refresh_token', return_value=True,
        ) as revoke:
            response = self.client.post(reverse('xero_disconnect'), follow=True)
        revoke.assert_called_once_with('old-refresh')
        self.assertNotContains(response, 'Connected apps')
        self.assertFalse(XeroConnection.get_connection().is_active)

    def test_disconnect_says_what_to_do_when_xero_does_not_confirm(self):
        with patch(
            'xero_integration.views.XeroClient.revoke_refresh_token', return_value=False,
        ):
            response = self.client.post(reverse('xero_disconnect'), follow=True)
        self.assertContains(response, 'Connected apps')
        self.assertFalse(XeroConnection.get_connection().is_active)


class OrganisationTagTests(TestCase):
    """4. Records from another organisation are not used."""

    def setUp(self):
        conn = _real_connection()
        conn.xero_tenant_id = 'org-b'
        conn.save()
        self.invoice = _invoice()

    def test_a_contact_link_from_another_organisation_is_looked_up_again(self):
        XeroContactMapping.objects.filter(owner=self.invoice.owner).update(xero_tenant_id='org-a')
        client = MagicMock()
        client.find_contact_by_name.return_value = {'ContactID': 'b-contact', 'Name': 'Alice'}
        with patch('xero_integration.services.XeroClient', return_value=client):
            contact_id = XeroContactService.ensure_contact_exists(self.invoice.owner)
        self.assertEqual(contact_id, 'b-contact')
        mapping = XeroContactMapping.objects.get(owner=self.invoice.owner)
        self.assertEqual(mapping.xero_tenant_id, 'org-b')

    def test_a_push_records_the_organisation(self):
        with patch('xero_integration.services.XeroClient', return_value=_pushing_client()):
            sync = push_invoice_to_xero(self.invoice)
        self.assertEqual(sync.xero_tenant_id, 'org-b')

    def test_the_nightly_check_skips_another_organisations_invoices(self):
        self.invoice.mark_as_sent()
        XeroInvoiceSync.objects.create(
            invoice=self.invoice, xero_invoice_id='a-1',
            sync_status=XeroInvoiceSync.SyncStatus.PUSHED, xero_tenant_id='org-a',
        )
        client = MagicMock()
        with patch('xero_integration.services.XeroClient', return_value=client):
            summary = sync_xero_invoice_statuses()
        client.get_invoice.assert_not_called()
        self.assertIn('Checked 0', summary)


class PaidInXeroTests(TestCase):
    """5. A second check of a paid invoice records no second payment.

    This runs the two checks one after the other. The race itself (two at
    the same moment) needs two database connections, which SQLite tests
    cannot give; the row lock in check_xero_invoice_status covers it.
    """

    def test_two_checks_from_the_same_old_view_pay_once(self):
        _real_connection()
        invoice = _invoice()
        invoice.mark_as_sent()
        XeroInvoiceSync.objects.create(
            invoice=invoice, xero_invoice_id='x-1',
            sync_status=XeroInvoiceSync.SyncStatus.PUSHED,
        )
        # Both loaded while the invoice was still unpaid, as two staff
        # pages (or a page and the nightly check) would be.
        first = XeroInvoiceSync.objects.select_related('invoice').get(invoice=invoice)
        second = XeroInvoiceSync.objects.select_related('invoice').get(invoice=invoice)
        client = MagicMock()
        client.get_invoice.return_value = {'Status': 'PAID'}
        with patch('xero_integration.services.XeroClient', return_value=client):
            check_xero_invoice_status(first)
            check_xero_invoice_status(second)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.assertEqual(invoice.payments.count(), 1)
        self.assertEqual(invoice.amount_paid, invoice.total)
