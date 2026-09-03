"""Tests for the ``check_analytics`` management command.

The command exists to give a straight answer about a live deployment, so the
thing worth testing is that it does not lie: it must exit non-zero on a broken
setup and must not report a test event as sent when the network refused it.
"""

from io import StringIO
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase, override_settings


def run(*args):
    """Run the command. Returns (exit_code, output)."""
    out = StringIO()
    code = 0
    try:
        call_command('check_analytics', *args, stdout=out, stderr=out)
    except SystemExit as exc:
        code = exc.code
    return code, out.getvalue()


@override_settings(POSTHOG_API_KEY='')
class AnalyticsOffTests(TestCase):
    def test_reports_off_and_succeeds(self):
        # An unset key is a valid state, not a fault.
        code, out = run()
        self.assertEqual(code, 0)
        self.assertIn('Analytics is OFF', out)

    def test_does_not_send_anything(self):
        with patch('requests.post') as post:
            run('--send-test-event')
        post.assert_not_called()


@override_settings(POSTHOG_API_KEY='phc_valid_looking_key')
class ConfigReportTests(TestCase):
    def test_key_is_masked(self):
        _, out = run()
        self.assertNotIn('phc_valid_looking_key', out)
        self.assertIn('phc_vali', out)

    def test_healthy_setup_passes(self):
        code, out = run()
        self.assertEqual(code, 0)
        self.assertIn('All checks passed', out)
        self.assertIn('registered', out)
        self.assertIn('connected', out)

    @override_settings(POSTHOG_API_KEY='phx_personal_key')
    def test_wrong_key_kind_fails(self):
        code, out = run()
        self.assertEqual(code, 1)
        self.assertIn('does not start with "phc_"', out)

    def test_missing_context_processor_fails(self):
        templates = [dict(settings.TEMPLATES[0])]
        templates[0]['OPTIONS'] = dict(templates[0]['OPTIONS'])
        templates[0]['OPTIONS']['context_processors'] = [
            p for p in templates[0]['OPTIONS']['context_processors']
            if p != 'core.analytics.template_context'
        ]
        with override_settings(TEMPLATES=templates):
            code, out = run()
        self.assertEqual(code, 1)
        self.assertIn('MISSING', out)


@override_settings(POSTHOG_API_KEY='phc_valid_looking_key')
class TestEventTests(TestCase):
    def response(self, status, text='{"status":1}'):
        resp = MagicMock()
        resp.status_code = status
        resp.text = text
        return resp

    def test_accepted_event_passes(self):
        with patch('requests.post', return_value=self.response(200)) as post:
            code, out = run('--send-test-event')
        self.assertEqual(code, 0)
        self.assertIn('accepted (HTTP 200)', out)
        # Posts to the configured host, and never creates a person profile.
        url, = post.call_args[0]
        self.assertTrue(url.startswith(settings.POSTHOG_HOST))
        payload = post.call_args[1]['json']
        self.assertIs(payload['properties']['$process_person_profile'], False)
        self.assertEqual(payload['api_key'], 'phc_valid_looking_key')

    def test_rejected_key_fails(self):
        bad = self.response(401, '{"code":"invalid_api_key"}')
        with patch('requests.post', return_value=bad):
            code, out = run('--send-test-event')
        self.assertEqual(code, 1)
        self.assertIn('REJECTED (HTTP 401)', out)
        self.assertIn('other region', out)

    def test_unreachable_host_is_not_reported_as_sent(self):
        # The bug this guards: the SDK queues events and returns before the
        # network call, so it reports success on a blocked egress.
        with patch('requests.post', side_effect=OSError('tunnel refused')):
            code, out = run('--send-test-event')
        self.assertEqual(code, 1)
        self.assertIn('NOT SENT', out)
        self.assertNotIn('accepted', out)

    def test_unexpected_status_fails(self):
        with patch('requests.post', return_value=self.response(500, 'boom')):
            code, out = run('--send-test-event')
        self.assertEqual(code, 1)
        self.assertIn('Unexpected response', out)

    def test_reach_probe_reports_failure(self):
        with patch('requests.get', side_effect=OSError('blocked')):
            code, out = run('--reach')
        self.assertEqual(code, 1)
        self.assertIn('UNREACHABLE', out)
