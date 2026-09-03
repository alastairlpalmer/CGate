"""Tests for the same-origin PostHog proxy.

The proxy exists so ad blockers cannot see the tracker. What matters is that
it forwards faithfully, never leaks this app's cookies upstream, refuses to
be an open proxy, and vanishes when analytics is off.
"""

from unittest.mock import MagicMock, patch

from django.test import Client, TestCase, override_settings

from core import analytics


def upstream_response(status=200, content=b'ok', headers=None):
    resp = MagicMock()
    resp.status_code = status
    resp.content = content
    resp.headers = headers or {'Content-Type': 'text/plain'}
    return resp


@override_settings(
    POSTHOG_API_KEY='phc_test',
    POSTHOG_PROXY=True,
    POSTHOG_HOST='https://eu.i.posthog.com',
    POSTHOG_ASSET_HOST='https://eu-assets.i.posthog.com',
)
class ProxyForwardingTests(TestCase):
    def test_static_goes_to_asset_host(self):
        js = upstream_response(content=b'// posthog', headers={
            'Content-Type': 'application/javascript',
            'Cache-Control': 'public, max-age=60',
            'Set-Cookie': 'nope=1',
            'Content-Encoding': 'gzip',
        })
        with patch('requests.request', return_value=js) as req:
            resp = self.client.get('/ingest/static/array.js')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b'// posthog')
        self.assertEqual(resp['Content-Type'], 'application/javascript')
        # Caching headers pass through so the browser keeps array.js.
        self.assertEqual(resp['Cache-Control'], 'public, max-age=60')
        # Cookies and the already-decoded encoding do not.
        self.assertNotIn('Set-Cookie', resp)
        self.assertNotIn('Content-Encoding', resp)
        method, url = req.call_args[0]
        self.assertEqual(method, 'GET')
        self.assertEqual(url, 'https://eu-assets.i.posthog.com/static/array.js')

    def test_events_go_to_event_host_with_body_and_query(self):
        with patch('requests.request', return_value=upstream_response()) as req:
            resp = self.client.post(
                '/ingest/i/v0/e/?compression=gzip-js&ver=1.2',
                data=b'\x1f\x8bpayload',
                content_type='text/plain',
            )
        self.assertEqual(resp.status_code, 200)
        method, url = req.call_args[0]
        kwargs = req.call_args[1]
        self.assertEqual(method, 'POST')
        self.assertEqual(url, 'https://eu.i.posthog.com/i/v0/e/')
        self.assertEqual(kwargs['data'], b'\x1f\x8bpayload')
        self.assertIn('compression=gzip-js', kwargs['params'])
        self.assertEqual(kwargs['headers']['Content-Type'], 'text/plain')
        self.assertFalse(kwargs['allow_redirects'])

    def test_session_cookie_never_leaves_this_origin(self):
        self.client.cookies['sessionid'] = 'secret-session'
        self.client.cookies['csrftoken'] = 'secret-token'
        with patch('requests.request', return_value=upstream_response()) as req:
            self.client.get('/ingest/flags/', HTTP_COOKIE='sessionid=secret')
        headers = req.call_args[1]['headers']
        self.assertNotIn('Cookie', headers)
        self.assertNotIn('secret', str(headers))

    def test_client_ip_forwarded(self):
        with patch('requests.request', return_value=upstream_response()) as req:
            self.client.get('/ingest/flags/', HTTP_X_FORWARDED_FOR='203.0.113.9, 10.0.0.1')
        self.assertEqual(req.call_args[1]['headers']['X-Forwarded-For'], '203.0.113.9')

    def test_post_without_csrf_token_is_accepted(self):
        # posthog-js cannot carry a Django CSRF token. The test client only
        # enforces CSRF when asked, so ask.
        strict = Client(enforce_csrf_checks=True)
        with patch('requests.request', return_value=upstream_response()):
            resp = strict.post('/ingest/i/v0/e/', data=b'{}', content_type='text/plain')
        self.assertEqual(resp.status_code, 200)

    def test_anonymous_visitor_can_reach_it(self):
        # $pageleave fires after sign-out; the proxy must not require login.
        with patch('requests.request', return_value=upstream_response()):
            resp = self.client.post('/ingest/i/v0/e/', data=b'{}', content_type='text/plain')
        self.assertEqual(resp.status_code, 200)

    def test_upstream_status_passes_through(self):
        with patch('requests.request', return_value=upstream_response(status=401, content=b'bad key')):
            resp = self.client.post('/ingest/i/v0/e/', data=b'{}', content_type='text/plain')
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.content, b'bad key')

    def test_upstream_failure_is_a_502_not_a_crash(self):
        import requests as rq
        with patch('requests.request', side_effect=rq.ConnectionError('down')):
            resp = self.client.get('/ingest/static/array.js')
        self.assertEqual(resp.status_code, 502)


@override_settings(POSTHOG_API_KEY='phc_test', POSTHOG_PROXY=True)
class ProxySafetyTests(TestCase):
    def test_rejects_path_traversal(self):
        with patch('requests.request') as req:
            resp = self.client.get('/ingest/static/../../etc/passwd')
        # Django normalises the URL before routing, so either the route misses
        # or the view refuses. Both are a 404 with no upstream call.
        self.assertEqual(resp.status_code, 404)
        req.assert_not_called()

    def test_rejects_unsupported_methods(self):
        with patch('requests.request') as req:
            resp = self.client.delete('/ingest/i/v0/e/')
        self.assertEqual(resp.status_code, 405)
        req.assert_not_called()

    @override_settings(POSTHOG_PROXY=False)
    def test_404_when_proxy_switched_off(self):
        with patch('requests.request') as req:
            resp = self.client.get('/ingest/static/array.js')
        self.assertEqual(resp.status_code, 404)
        req.assert_not_called()

    @override_settings(POSTHOG_API_KEY='')
    def test_404_when_analytics_off(self):
        with patch('requests.request') as req:
            resp = self.client.get('/ingest/static/array.js')
        self.assertEqual(resp.status_code, 404)
        req.assert_not_called()


@override_settings(POSTHOG_API_KEY='phc_test')
class ContextProcessorTests(TestCase):
    def setUp(self):
        from core.roles_testutils import make_admin
        self.user = make_admin(username='rider')
        self.request = type('R', (), {'user': self.user})()

    @override_settings(POSTHOG_PROXY=True)
    def test_browser_gets_proxy_path_when_on(self):
        cfg = analytics.template_context(self.request)['posthog']
        self.assertEqual(cfg['api_host'], '/ingest')
        self.assertEqual(cfg['asset_host'], '/ingest')
        # The toolbar still needs the real dashboard.
        self.assertTrue(cfg['ui_host'].startswith('https://'))

    @override_settings(POSTHOG_PROXY=False)
    def test_browser_gets_real_hosts_when_off(self):
        cfg = analytics.template_context(self.request)['posthog']
        self.assertTrue(cfg['api_host'].startswith('https://'))
        self.assertTrue(cfg['asset_host'].startswith('https://'))
