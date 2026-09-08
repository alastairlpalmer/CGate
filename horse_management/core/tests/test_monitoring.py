"""Sentry error monitoring, and what it must not send.

The app holds owner names, email addresses, postal addresses and invoice
figures. An error report that carries those copies personal data to a third
party with no record of it going, so the scrubbing here is the point of the
feature as much as the reporting is.
"""

from django.test import SimpleTestCase

from core import monitoring

FAKE_DSN = 'https://examplePublicKey@o0.ingest.sentry.io/0'


class InitTests(SimpleTestCase):
    def test_no_dsn_means_sentry_is_not_started(self):
        """Local runs and the test suite must send nothing anywhere."""
        self.assertFalse(monitoring.init(dsn=''))
        self.assertFalse(monitoring.init(dsn=None))

    def test_a_dsn_starts_sentry(self):
        self.assertTrue(monitoring.init(dsn=FAKE_DSN))
        # Leave no client behind for the rest of the suite.
        import sentry_sdk
        sentry_sdk.init(dsn='')


class SensitiveKeyTests(SimpleTestCase):
    def test_this_apps_own_secrets_are_recognised(self):
        for key in (
            'SECRET_KEY', 'FIELD_ENCRYPTION_KEYS', 'DATABASE_URL',
            'XERO_CLIENT_SECRET', 'EMAIL_HOST_PASSWORD', 'access_token',
            'refresh_token', 'oauth_state', 'CELERY_BROKER_URL',
        ):
            with self.subTest(key=key):
                self.assertTrue(monitoring._is_sensitive(key))

    def test_variants_the_exact_list_would_miss(self):
        for key in (
            'xero_refresh_token', 'db_password', 'MY_API_KEY',
            'stripe_secret', 'Authorization',
        ):
            with self.subTest(key=key):
                self.assertTrue(monitoring._is_sensitive(key))

    def test_ordinary_keys_are_left_alone(self):
        """Over-scrubbing makes a report useless to debug from."""
        for key in ('horse_id', 'invoice_number', 'url', 'status_code'):
            with self.subTest(key=key):
                self.assertFalse(monitoring._is_sensitive(key))

    def test_a_non_string_key_does_not_raise(self):
        self.assertFalse(monitoring._is_sensitive(3))
        self.assertFalse(monitoring._is_sensitive(None))


class ScrubTests(SimpleTestCase):
    def test_secrets_are_masked_at_any_depth(self):
        """A traceback frame holds local variables, so a secret can sit
        several levels inside the event structure."""
        event = {
            'exception': {'values': [{'stacktrace': {'frames': [{
                'vars': {
                    'SECRET_KEY': 'the-real-key',
                    'horse_name': 'Dobbin',
                },
            }]}}]},
        }
        scrubbed = monitoring._scrub(event)
        frame = scrubbed['exception']['values'][0]['stacktrace']['frames'][0]
        self.assertEqual(frame['vars']['SECRET_KEY'], monitoring.MASK)
        self.assertEqual(frame['vars']['horse_name'], 'Dobbin')

    def test_lists_and_tuples_are_walked(self):
        event = {'extra': [{'refresh_token': 'abc'}, ('x', {'password': 'p'})]}
        scrubbed = monitoring._scrub(event)
        self.assertEqual(scrubbed['extra'][0]['refresh_token'], monitoring.MASK)
        self.assertEqual(scrubbed['extra'][1][1]['password'], monitoring.MASK)

    def test_a_deeply_nested_structure_terminates(self):
        """Depth limit: a self-referencing repr must not hang the process
        that is already handling an error."""
        event = current = {}
        for _ in range(200):
            current['next'] = {}
            current = current['next']
        monitoring._scrub(event)  # must return, not recurse forever


class QueryStringTests(SimpleTestCase):
    def test_the_query_string_is_dropped_from_the_url(self):
        """The search box puts owner and horse names into ?q=."""
        event = {'request': {
            'url': 'https://yardway.example/horses/?q=Jane+Smith',
            'query_string': 'q=Jane+Smith',
        }}
        cleaned = monitoring._strip_query_string(event)
        self.assertEqual(cleaned['request']['url'], 'https://yardway.example/horses/')
        self.assertNotIn('query_string', cleaned['request'])

    def test_a_url_without_a_query_string_is_unchanged(self):
        event = {'request': {'url': 'https://yardway.example/horses/'}}
        self.assertEqual(
            monitoring._strip_query_string(event)['request']['url'],
            'https://yardway.example/horses/',
        )

    def test_an_event_with_no_request_is_fine(self):
        self.assertEqual(monitoring._strip_query_string({}), {})


class BeforeSendTests(SimpleTestCase):
    def test_it_scrubs_and_strips_in_one_pass(self):
        event = {
            'request': {'url': 'https://yardway.example/search/?q=Dobbin'},
            'extra': {'DATABASE_URL': 'postgres://user:pass@host/db'},
        }
        sent = monitoring.before_send(event, hint={})
        self.assertEqual(sent['request']['url'], 'https://yardway.example/search/')
        self.assertEqual(sent['extra']['DATABASE_URL'], monitoring.MASK)

    def test_a_failure_inside_scrubbing_drops_the_event(self):
        """Dropping one report loses information. Sending an unscrubbed one
        could leak a live credential, so the failure must fail closed."""
        original = monitoring._scrub
        monitoring._scrub = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('boom'))
        try:
            with self.assertLogs('core.monitoring', level='ERROR'):
                self.assertIsNone(monitoring.before_send({'a': 1}, hint={}))
        finally:
            monitoring._scrub = original
