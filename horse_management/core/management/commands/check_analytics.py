"""
Report whether PostHog analytics is configured, and prove it can send.

Run this inside the running container (Railway shell, or `vercel dev`) when
you want a straight answer to "is tracking on?". A dashboard shows you that a
variable exists; this shows you that the app actually read it.

Usage:
    python manage.py check_analytics                  # config report, no network
    python manage.py check_analytics --send-test-event # also send one real event
    python manage.py check_analytics --reach           # also probe the host

Exit code is 0 when analytics is on and healthy, 1 when something is wrong.
An unset POSTHOG_API_KEY is reported as OFF and exits 0 — that is a valid
state, not a fault.
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from core import analytics

# Event and person used by --send-test-event. Fixed names so the events are
# easy to find and filter out in PostHog.
TEST_EVENT = 'analytics_check'
TEST_DISTINCT_ID = 'analytics-check'
# PostHog's current capture endpoint. The SDK posts here too.
CAPTURE_PATH = '/i/v0/e/'


def mask(key: str) -> str:
    """Show enough of the key to tell two projects apart, and no more."""
    if not key:
        return '(unset)'
    if len(key) <= 12:
        return key[:4] + '…'
    return f'{key[:8]}…{key[-4:]} ({len(key)} chars)'


class Command(BaseCommand):
    help = 'Report PostHog analytics configuration and optionally send a test event.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--send-test-event',
            action='store_true',
            help='Send one anonymous %s event through the real client.' % TEST_EVENT,
        )
        parser.add_argument(
            '--reach',
            action='store_true',
            help='Probe the PostHog host over HTTPS to rule out a blocked egress.',
        )

    def handle(self, *args, **options):
        self.problems = []

        self.stdout.write(self.style.MIGRATE_HEADING('PostHog analytics check'))
        self.stdout.write('')

        on = self.report_config()
        self.report_package()
        self.report_wiring()

        if not on:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                'Analytics is OFF. Set POSTHOG_API_KEY to switch it on.'
            ))
            return

        if options['reach']:
            self.report_reachability()
        if options['send_test_event']:
            self.send_test_event()

        self.stdout.write('')
        if self.problems:
            for problem in self.problems:
                self.stdout.write(self.style.ERROR(f'  FAIL  {problem}'))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS('All checks passed.'))

    # -- sections ---------------------------------------------------------

    def report_config(self) -> bool:
        """Print the resolved settings. Returns True when analytics is on."""
        key = getattr(settings, 'POSTHOG_API_KEY', '')
        on = analytics.is_enabled()

        self.stdout.write('Configuration')
        self.row('POSTHOG_API_KEY', mask(key))
        self.row('Analytics', 'ON' if on else 'OFF')
        self.row('Event host', settings.POSTHOG_HOST)
        self.row('Asset host', settings.POSTHOG_ASSET_HOST)
        self.row('Dashboard', settings.POSTHOG_UI_HOST)
        self.row('Browser proxy', (
            f'on, via {settings.POSTHOG_PROXY_PATH}/' if settings.POSTHOG_PROXY
            else 'off (browser talks to posthog.com directly)'
        ))
        self.row('Session replay', 'on' if settings.POSTHOG_SESSION_RECORDING else 'off')
        self.row('Usernames sent', 'yes' if settings.POSTHOG_SEND_USERNAMES else 'no')
        self.row('Server send mode', 'sync' if settings.POSTHOG_SYNC_MODE else 'background')
        self.row('posthog-js debug', 'on' if settings.POSTHOG_DEBUG else 'off')
        self.stdout.write('')

        if on and not key.startswith('phc_'):
            self.problems.append(
                'POSTHOG_API_KEY does not start with "phc_". A project key looks '
                'like phc_... — a personal key (phx_...) will not work.'
            )
        return on

    def report_package(self):
        self.stdout.write('SDK')
        try:
            import posthog
        except ImportError:
            self.row('posthog package', 'NOT INSTALLED')
            self.problems.append(
                'The posthog package is missing. Run pip install -r requirements.txt.'
            )
        else:
            self.row('posthog package', getattr(posthog, 'VERSION', 'installed'))
        self.stdout.write('')

    def report_wiring(self):
        """Confirm the two halves are plugged in, not merely importable."""
        from django.contrib.auth.signals import user_logged_in

        processors = settings.TEMPLATES[0]['OPTIONS']['context_processors']
        has_processor = 'core.analytics.template_context' in processors
        self.stdout.write('Wiring')
        self.row('Browser snippet', 'registered' if has_processor else 'MISSING')
        if not has_processor:
            self.problems.append(
                'core.analytics.template_context is not in TEMPLATES '
                'context_processors, so no page will load the tracker.'
            )

        # Receivers are keyed on their dispatch_uid, which core.signals sets.
        # Index rather than unpack: the entry tuple gained a third member in
        # Django 5 and may grow again.
        connected = any(
            entry[0][0] == 'core.analytics.user_logged_in'
            for entry in user_logged_in.receivers
        )
        self.row('Sign-in signal', 'connected' if connected else 'MISSING')
        if not connected:
            self.problems.append(
                'The user_logged_in receiver is not connected. Check that '
                'core.apps.CoreConfig.ready imports core.signals.'
            )
        self.stdout.write('')

    def report_reachability(self):
        """Rule out a blocked egress before blaming the key."""
        self.stdout.write('Reachability')
        try:
            import requests
        except ImportError:  # pragma: no cover - requests is a dependency
            self.row(settings.POSTHOG_HOST, 'SKIPPED (requests missing)')
            self.stdout.write('')
            return
        for host in (settings.POSTHOG_HOST, settings.POSTHOG_ASSET_HOST):
            try:
                resp = requests.get(host, timeout=10)
                self.row(host, f'reachable (HTTP {resp.status_code})')
            except Exception as exc:
                self.row(host, 'UNREACHABLE')
                self.problems.append(f'Cannot reach {host}: {exc}')
        self.stdout.write('')

    def send_test_event(self):
        """Send one event and report what PostHog actually said.

        This posts straight to the capture endpoint rather than going through
        the SDK. The SDK queues events and returns before the network call, so
        it reports success even when the host is unreachable — no use at all
        for answering "is it working?". A direct post gives a status code:
        200 means PostHog accepted the event, 401 means the key is wrong.
        """
        self.stdout.write('Test event')
        try:
            import requests
        except ImportError:  # pragma: no cover - requests is a dependency
            self.row(TEST_EVENT, 'SKIPPED (requests missing)')
            self.problems.append('Cannot send a test event without requests.')
            self.stdout.write('')
            return

        url = settings.POSTHOG_HOST.rstrip('/') + CAPTURE_PATH
        payload = {
            'api_key': settings.POSTHOG_API_KEY,
            'event': TEST_EVENT,
            'distinct_id': TEST_DISTINCT_ID,
            'properties': {
                'source': 'check_analytics',
                # Keep the check out of the person database.
                '$process_person_profile': False,
            },
        }
        try:
            resp = requests.post(url, json=payload, timeout=15)
        except Exception as exc:
            self.row(TEST_EVENT, 'NOT SENT')
            self.problems.append(
                f'Cannot reach {url}: {exc}\n'
                f'        Egress to PostHog is blocked, or the host setting is wrong.'
            )
            self.stdout.write('')
            return

        body = (resp.text or '')[:200]
        if resp.status_code == 200:
            self.row(TEST_EVENT, f'accepted (HTTP 200) {body}')
            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS(
                f'  PostHog accepted the event. Open {settings.POSTHOG_UI_HOST}\n'
                f'  > Activity and you should see "{TEST_EVENT}" within a minute.'
            ))
        elif resp.status_code == 401:
            self.row(TEST_EVENT, 'REJECTED (HTTP 401)')
            self.problems.append(
                f'PostHog rejected the key: {body}\n'
                f'        The key is wrong, or it belongs to a project in the '
                f'other region. Check POSTHOG_HOST matches where the project lives.'
            )
        else:
            self.row(TEST_EVENT, f'HTTP {resp.status_code}')
            self.problems.append(
                f'Unexpected response from {url}: HTTP {resp.status_code} {body}'
            )
        self.stdout.write('')

    # -- output -----------------------------------------------------------

    def row(self, label, value):
        self.stdout.write(f'  {label:<20} {value}')
