"""Confirm it is you (core.reauth), pre-launch review: a signed-in session
could replace the authenticator, change the email address or link a Google
account with no password."""

import time

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.middleware import SESSION_STARTED_KEY
from core.roles_testutils import make_user_with_access
from core.tests.test_account_security import PW, enrol

User = get_user_model()


class ReauthTests(TestCase):
    def setUp(self):
        self.user = make_user_with_access('rider@example.com', dashboard='full')
        self.user.email = 'rider@example.com'
        self.user.set_password(PW)
        self.user.save()
        self.client.force_login(self.user)

    def age_the_sign_in(self):
        session = self.client.session
        session[SESSION_STARTED_KEY] = int(time.time()) - 3600
        session.save()

    def details(self, email, secret=''):
        return self.client.post(reverse('profile'), {
            'action': 'details', 'first_name': 'R', 'last_name': 'P', 'email': email,
            'display_size': 'compact', 'confirm_secret': secret,
        })

    def test_a_fresh_sign_in_counts(self):
        self.details('new@example.com')
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'new@example.com')

    def test_an_old_session_cannot_change_the_email_without_the_password(self):
        self.age_the_sign_in()
        response = self.details('thief@example.com')
        self.assertContains(response, 'To change your email address, enter your password')
        self.details('thief@example.com', secret='wrong')
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'rider@example.com')
        self.details('new@example.com', secret=PW)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'new@example.com')

    def test_other_details_need_no_password(self):
        self.age_the_sign_in()
        self.details('rider@example.com')
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'R')

    def test_replacing_the_authenticator_needs_a_confirmation(self):
        device, codes = enrol(self.user)
        self.age_the_sign_in()
        response = self.client.get(reverse('mfa_setup'))
        self.assertTrue(response['Location'].startswith(reverse('reauth')))
        response = self.client.post(reverse('reauth'), {'code': codes[0], 'next': reverse('mfa_setup')})
        self.assertRedirects(response, reverse('mfa_setup'), fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse('mfa_setup')), '<svg')

    def test_a_wrong_password_does_not_confirm(self):
        self.age_the_sign_in()
        response = self.client.post(reverse('reauth'), {'password': 'nope', 'next': '/'})
        self.assertContains(response, 'That did not match')

    @override_settings(GOOGLE_SIGN_IN_ENABLED=True)
    def test_connecting_google_needs_a_confirmation(self):
        self.age_the_sign_in()
        response = self.client.post(reverse('google_start'), {'mode': 'connect'})
        self.assertTrue(response['Location'].startswith(reverse('reauth')))
