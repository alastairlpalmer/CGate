"""The client's address behind the platform proxy (pre-launch review).

REMOTE_ADDR is the proxy, the same for every visitor, so one script could
use up the password-reset limit for everyone."""

from django.test import RequestFactory, SimpleTestCase, override_settings

from core.audit import client_ip


class ClientIpTests(SimpleTestCase):
    def request(self, forwarded=None):
        meta = {'REMOTE_ADDR': '10.0.0.1'}
        if forwarded is not None:
            meta['HTTP_X_FORWARDED_FOR'] = forwarded
        return RequestFactory().get('/', **meta)

    @override_settings(CLIENT_IP_PROXY_COUNT=1)
    def test_the_entry_the_proxy_added_is_used(self):
        self.assertEqual(client_ip(self.request('203.0.113.9')), '203.0.113.9')

    @override_settings(CLIENT_IP_PROXY_COUNT=1)
    def test_an_entry_the_client_sent_is_not_trusted(self):
        self.assertEqual(client_ip(self.request('1.2.3.4, 203.0.113.9')), '203.0.113.9')

    @override_settings(CLIENT_IP_PROXY_COUNT=1)
    def test_no_header_uses_remote_addr(self):
        self.assertEqual(client_ip(self.request()), '10.0.0.1')

    @override_settings(CLIENT_IP_PROXY_COUNT=0)
    def test_zero_proxies_uses_remote_addr(self):
        self.assertEqual(client_ip(self.request('203.0.113.9')), '10.0.0.1')

    @override_settings(CLIENT_IP_PROXY_COUNT=2)
    def test_too_few_entries_uses_remote_addr(self):
        self.assertEqual(client_ip(self.request('203.0.113.9')), '10.0.0.1')
