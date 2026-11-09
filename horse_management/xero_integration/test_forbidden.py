"""Xero 403 "AuthenticationUnsuccessful" on a push.

The token refreshed without error, so the connection looked fine, but
Xero refused every call for the organisation: Cgate had been removed from
it in Xero. The page showed only "Xero API error: Xero API error: 403
(AuthenticationUnsuccessful)" and a Retry button that could never work.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from xero_integration.client import (
    XeroAPIError,
    XeroClient,
    XeroOrganisationAccessError,
    XeroTokenExpiredError,
)
from xero_integration.models import XeroConnection


def _connection():
    conn = XeroConnection.get_connection()
    conn.is_active = True
    conn.xero_tenant_id = 'tenant-a'
    conn.xero_tenant_name = 'Colgate Livery'
    conn.access_token = 'access'
    conn.refresh_token = 'refresh'
    conn.token_expires_at = timezone.now() + timedelta(hours=1)
    conn.connected_at = timezone.now()
    conn.last_refreshed_at = timezone.now()
    conn.save()
    return conn


def _response(status, body):
    response = MagicMock(status_code=status, headers={})
    response.json.return_value = body
    response.text = str(body)
    return response


FORBIDDEN = _response(403, {'Title': 'Forbidden', 'Detail': 'AuthenticationUnsuccessful'})


class ForbiddenTests(TestCase):
    def setUp(self):
        _connection()

    @patch('xero_integration.client.requests.get')
    @patch('xero_integration.client.requests.request', return_value=FORBIDDEN)
    def test_organisation_gone_marks_connection_for_reconnect(self, _request, get):
        get.return_value = _response(200, [{'tenantId': 'tenant-b', 'tenantName': 'Other'}])

        with self.assertRaises(XeroOrganisationAccessError) as caught:
            XeroClient().find_contact_by_name('Rebecca Osbourne')

        self.assertIsInstance(caught.exception, XeroTokenExpiredError)
        self.assertEqual(caught.exception.status_code, 403)
        self.assertIn('Colgate Livery', str(caught.exception))
        self.assertIn('Connect to Xero again', str(caught.exception))
        self.assertFalse(XeroConnection.get_connection().is_connected)

    @patch('xero_integration.client.requests.get')
    @patch('xero_integration.client.requests.request', return_value=FORBIDDEN)
    def test_organisation_still_listed_keeps_connection(self, _request, get):
        get.return_value = _response(200, [{'tenantId': 'tenant-a'}])

        with self.assertRaises(XeroAPIError) as caught:
            XeroClient().find_contact_by_name('Rebecca Osbourne')

        self.assertNotIsInstance(caught.exception, XeroTokenExpiredError)
        self.assertEqual(caught.exception.status_code, 403)
        self.assertIn('AuthenticationUnsuccessful', str(caught.exception))
        self.assertIn('Standard or Adviser', str(caught.exception))
        self.assertTrue(XeroConnection.get_connection().is_connected)

    @patch('xero_integration.client.requests.get')
    @patch('xero_integration.client.requests.request', return_value=FORBIDDEN)
    def test_connections_check_fails_keeps_connection(self, _request, get):
        get.return_value = _response(500, {})

        with self.assertRaises(XeroAPIError) as caught:
            XeroClient().find_contact_by_name('Rebecca Osbourne')

        self.assertEqual(caught.exception.status_code, 403)
        self.assertTrue(XeroConnection.get_connection().is_connected)


class PushMessageTests(TestCase):
    def test_message_has_no_doubled_prefix(self):
        from django.contrib.messages import get_messages

        from core.models import Owner
        from invoicing.models import Invoice
        from django.contrib.auth import get_user_model

        _connection()
        user = get_user_model().objects.create_superuser('admin', 'a@example.com', 'pw')
        self.client.force_login(user)
        owner = Owner.objects.create(name='Rebecca Osbourne', email='r@example.com')
        today = timezone.localdate()
        invoice = Invoice.objects.create(
            owner=owner, invoice_number='INV09999',
            period_start=today - timedelta(days=30), period_end=today,
            due_date=today + timedelta(days=30),
        )
        error = XeroAPIError('Xero API error: 400 (bad)', status_code=400)
        with patch('xero_integration.views.push_invoice_to_xero', side_effect=error):
            response = self.client.post(f'/xero/push/{invoice.pk}/')

        text = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertEqual(text, ['Could not push to Xero. Xero API error: 400 (bad)'])
