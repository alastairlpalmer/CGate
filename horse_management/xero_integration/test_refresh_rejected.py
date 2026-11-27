"""An expired Xero connection shows as not connected (pre-launch review).

The deactivation was saved inside the locked block, and the error raised
there rolled it back."""

from unittest import mock

from django.test import TestCase

from xero_integration.client import XeroClient, XeroTokenExpiredError
from xero_integration.models import XeroConnection


class RefreshRejectedTests(TestCase):
    def test_invalid_grant_leaves_the_connection_inactive(self):
        conn = XeroConnection.objects.create(access_token='a', refresh_token='r', is_active=True)
        client = XeroClient.__new__(XeroClient)
        client.connection = conn
        rejected = mock.Mock(status_code=400, text='{"error":"invalid_grant"}')
        rejected.json.return_value = {'error': 'invalid_grant'}
        with mock.patch('requests.post', return_value=rejected):
            with self.assertRaises(XeroTokenExpiredError):
                client._refresh_access_token()
        conn.refresh_from_db()
        self.assertFalse(conn.is_active)
