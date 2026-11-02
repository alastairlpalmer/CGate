"""Sign-in and password-reset cases from the launch readiness plan
(docs/LAUNCH_READINESS_PLAN.md, P0)."""

from datetime import datetime, timedelta
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.test import TestCase
from django.urls import reverse

from core.roles_testutils import make_admin

PW = 'correct-horse-battery-staple'


class PlusAddressSignInTests(TestCase):
    """A '+' is a legal character in an email address. Nothing may strip
    it or treat it as a space."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='jo', email='jo+yard@example.com', password=PW,
        )

    def _sign_in(self, username):
        return self.client.post(reverse('login'), {'username': username, 'password': PW})

    def test_plus_address_signs_in(self):
        response = self._sign_in('jo+yard@example.com')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.user.pk)

    def test_plus_address_is_case_insensitive(self):
        response = self._sign_in('Jo+Yard@Example.com')
        self.assertEqual(response.status_code, 302)

    def test_address_without_the_plus_part_is_a_different_account(self):
        response = self._sign_in('jo@example.com')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)


class PasswordResetTokenTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='sam', email='sam@example.com', password=PW,
        )

    def test_timeout_is_one_hour(self):
        self.assertEqual(settings.PASSWORD_RESET_TIMEOUT, 60 * 60)

    def test_token_expires_after_the_timeout(self):
        token = default_token_generator.make_token(self.user)
        later = datetime.now() + timedelta(seconds=settings.PASSWORD_RESET_TIMEOUT + 60)
        with mock.patch.object(default_token_generator, '_now', return_value=later):
            self.assertFalse(default_token_generator.check_token(self.user, token))

    def test_token_works_inside_the_timeout(self):
        token = default_token_generator.make_token(self.user)
        self.assertTrue(default_token_generator.check_token(self.user, token))

    def test_token_works_once(self):
        token = default_token_generator.make_token(self.user)
        self.user.set_password('a-new-and-different-password')
        self.user.save()
        self.assertFalse(default_token_generator.check_token(self.user, token))


class HtmxCsrfHeaderTests(TestCase):
    """Every HTMX request carries the CSRF token from <body hx-headers>,
    so a new hx-post does not depend on its form remembering the token."""

    def test_body_sends_csrf_header(self):
        self.client.force_login(make_admin('csrfadmin'))
        body = self.client.get(reverse('dashboard')).content.decode()
        token = self.client.cookies[settings.CSRF_COOKIE_NAME].value
        self.assertIn('hx-headers=\'{"X-CSRFToken": "', body)
        self.assertNotIn('"X-CSRFToken": ""', body)
        self.assertTrue(token)
