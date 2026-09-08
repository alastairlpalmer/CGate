"""Brute-force protection on sign-in (django-axes).

axes is switched off for the rest of the suite (see test_settings.py) because
``Client.login()`` gives it no request object. These tests turn it back on and
drive the real sign-in view, which is the path a browser takes.
"""

from axes.handlers.proxy import AxesProxyHandler
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

PW = 'correct-horse-battery-staple'
BAD = 'not-the-password'


@override_settings(AXES_ENABLED=True, AXES_FAILURE_LIMIT=3, AXES_RESET_ON_SUCCESS=True)
class LoginLockoutTests(TestCase):
    def setUp(self):
        self.url = reverse('login')
        User = get_user_model()
        self.user = User.objects.create_user(
            username='jo@example.com', email='jo@example.com', password=PW,
        )
        # Attempts are stored in the database and the cache; a row left by
        # another test would make these assertions depend on ordering.
        AxesProxyHandler.reset_attempts()
        AxesProxyHandler.reset_logs()

    def tearDown(self):
        AxesProxyHandler.reset_attempts()
        AxesProxyHandler.reset_logs()

    def _attempt(self, password, username='jo@example.com'):
        return self.client.post(
            self.url, {'username': username, 'password': password},
        )

    def test_correct_password_signs_in(self):
        response = self._attempt(PW)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.user.pk)

    def test_lockout_at_the_failure_limit(self):
        # Below the limit the form is simply redisplayed with an error.
        for _ in range(2):
            self.assertEqual(self._attempt(BAD).status_code, 200)

        # The third failure reaches AXES_FAILURE_LIMIT and locks the account.
        locked = self._attempt(BAD)
        self.assertEqual(locked.status_code, 429)
        self.assertContains(locked, 'Too many sign-in attempts', status_code=429)

    def test_correct_password_is_refused_while_locked_out(self):
        """The point of the lockout: guessing stops working even if the
        attacker's next guess happens to be right."""
        for _ in range(3):
            self._attempt(BAD)

        response = self._attempt(PW)
        self.assertEqual(response.status_code, 429)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_a_success_before_the_limit_clears_the_count(self):
        """AXES_RESET_ON_SUCCESS: typos spread over weeks must not add up."""
        self._attempt(BAD)
        self.assertEqual(self._attempt(PW).status_code, 302)
        self.client.logout()

        # Counter is back to zero, so two more failures are still not a
        # lockout — without the reset, this third failure would be one.
        self._attempt(BAD)
        self._attempt(BAD)
        self.assertEqual(self._attempt(PW).status_code, 302)

    def test_lockout_does_not_spread_to_another_account(self):
        """Locking is keyed on the username. One account's failures must
        not stop everybody else signing in."""
        User = get_user_model()
        User.objects.create_user(
            username='sam@example.com', email='sam@example.com', password=PW,
        )
        for _ in range(3):
            self._attempt(BAD)

        response = self._attempt(PW, username='sam@example.com')
        self.assertEqual(response.status_code, 302)

    def test_username_case_does_not_create_a_second_allowance(self):
        """Sign-in is case-insensitive (core.auth_backends), so JO@… and
        jo@… must share one attempt counter — otherwise an attacker gets a
        fresh five guesses per capitalisation."""
        for _ in range(2):
            self._attempt(BAD, username='jo@example.com')

        # Third failure overall, first with this capitalisation.
        response = self._attempt(BAD, username='JO@EXAMPLE.COM')
        self.assertEqual(response.status_code, 429)

    def test_lockout_page_does_not_confirm_the_account_exists(self):
        """Anyone can reach the lockout page by guessing at an address, so
        it must not say whether that address is registered."""
        for _ in range(3):
            self._attempt(BAD, username='nobody@example.com')

        response = self._attempt(BAD, username='nobody@example.com')
        self.assertEqual(response.status_code, 429)
        self.assertNotContains(
            response, 'nobody@example.com', status_code=429,
        )
