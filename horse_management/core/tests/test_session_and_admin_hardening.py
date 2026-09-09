"""Two settings that limit what a stolen credential reaches.

Both are cheap and neither is a substitute for the password and role
checks that do the real work. They narrow the window and take the site
out of untargeted scanning.
"""

from django.conf import settings
from django.test import TestCase
from django.urls import reverse


class SessionLifetimeTests(TestCase):
    def test_a_session_lasts_a_week_not_a_month(self):
        """A session cookie is a bearer credential — whoever holds it is
        signed in. Thirty days of validity on a copied cookie is longer
        than a yard needs."""
        self.assertEqual(settings.SESSION_COOKIE_AGE, 7 * 24 * 60 * 60)

    def test_expiry_still_rolls_forward(self):
        """Weekly users must not be signed out. The seven days count from
        the last request, not from sign-in."""
        self.assertTrue(settings.SESSION_SAVE_EVERY_REQUEST)

    def test_the_cookie_stays_locked_down(self):
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, 'Lax')


class AdminUrlTests(TestCase):
    def test_the_admin_is_mounted_where_the_setting_says(self):
        self.assertEqual(reverse('admin:index'), f'/{settings.ADMIN_URL}')

    def test_the_setting_always_ends_in_a_slash(self):
        """Django's path() needs the trailing slash. Without this the
        admin would be reachable only at a URL nobody would guess to
        type, including its owner."""
        self.assertTrue(settings.ADMIN_URL.endswith('/'))

    def test_the_admin_is_never_mounted_at_the_site_root(self):
        """An empty ADMIN_URL would shadow the dashboard with the admin.
        The settings guard rejects it."""
        self.assertNotEqual(settings.ADMIN_URL, '')
