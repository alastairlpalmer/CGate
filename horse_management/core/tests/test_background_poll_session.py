"""The notification poll does not keep a session alive (pre-launch review).
SESSION_SAVE_EVERY_REQUEST rolled the idle timeout forward on each poll,
so an open tab nobody touched was never signed out."""

from django.contrib.sessions.models import Session
from django.test import TestCase
from django.urls import reverse

from core.roles_testutils import make_admin


class BackgroundPollTests(TestCase):
    def setUp(self):
        self.client.force_login(make_admin('poller'))
        self.key = self.client.session.session_key

    def expiry(self):
        return Session.objects.get(session_key=self.key).expire_date

    def test_a_background_poll_does_not_move_the_expiry(self):
        before = self.expiry()
        self.client.get(reverse('notification_badge'), HTTP_X_BACKGROUND='1', HTTP_HX_REQUEST='true')
        self.assertEqual(self.expiry(), before)

    def test_a_normal_request_moves_it(self):
        before = self.expiry()
        self.client.get(reverse('dashboard'))
        self.assertGreater(self.expiry(), before)

    def test_the_poller_sends_the_header(self):
        self.assertContains(self.client.get(reverse('dashboard')), '"X-Background": "1"')


class HistoryCacheTests(TestCase):
    def test_pages_are_not_copied_into_browser_storage(self):
        self.client.force_login(make_admin('historyadmin'))
        self.assertContains(self.client.get(reverse('dashboard')), '"historyCacheSize": 0')
