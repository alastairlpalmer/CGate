"""Account security: lockout recovery, password reset limits, session
lifetime, signing out everywhere, invitations and two-step sign-in.

docs/LAUNCH_READINESS_PLAN.md, P1 work package 1.
"""

import re
import time
from io import StringIO

from axes.handlers.proxy import AxesProxyHandler
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django_otp.oath import TOTP

from core import mfa
from core.account_security import is_locked_out
from core.middleware import SESSION_STARTED_KEY
from core.roles_testutils import make_admin, make_user_with_access
from core.views.account import invite_token_generator

User = get_user_model()
PW = 'correct-horse-battery-staple'


def totp_code(device, step_offset=0):
    """The code the user's app would show, ``step_offset`` steps from now."""
    totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    totp.time = time.time() + step_offset * device.step
    return f'{totp.token():0{device.digits}d}'


def enrol(user):
    """Give ``user`` a confirmed authenticator and recovery codes."""
    device = mfa.start_setup(user)
    codes = mfa.confirm_setup(user, device, totp_code(device))
    device.refresh_from_db()
    return device, codes


# ── Lockouts ─────────────────────────────────────────────────────────────────

@override_settings(AXES_ENABLED=True, AXES_FAILURE_LIMIT=3)
class LockoutRecoveryTests(TestCase):
    def setUp(self):
        AxesProxyHandler.reset_attempts()
        self.user = User.objects.create_user(username='cfo@example.com', email='cfo@example.com', password=PW)
        self.admin = make_admin('boss', email='boss@example.com')

    def tearDown(self):
        AxesProxyHandler.reset_attempts()
        AxesProxyHandler.reset_logs()

    def _lock(self):
        client = Client()
        for _ in range(3):
            client.post(reverse('login'), {'username': 'CFO@example.com', 'password': 'wrong'})
        self.assertTrue(is_locked_out(self.user))

    def test_admin_unlock_button_clears_the_lock(self):
        self._lock()
        self.client.force_login(self.admin)
        self.client.post(reverse('user_update', args=[self.user.pk]), {'unlock': '1'})
        self.assertFalse(is_locked_out(self.user))
        response = Client().post(reverse('login'), {'username': 'cfo@example.com', 'password': PW})
        self.assertEqual(response.status_code, 302)

    def test_user_page_shows_the_lock(self):
        self._lock()
        self.client.force_login(self.admin)
        response = self.client.get(reverse('user_update', args=[self.user.pk]))
        self.assertContains(response, 'Locked out')
        self.assertContains(response, 'name="unlock"')

    def test_password_reset_unlocks_the_account(self):
        """The person locked out by a stranger can get back in through their
        own mailbox, without waiting and without an administrator."""
        self._lock()
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        client = Client()
        response = client.get(reverse('password_reset_confirm', args=[uid, token]))
        new_pw = 'a-brand-new-long-password'
        client.post(response.url, {'new_password1': new_pw, 'new_password2': new_pw})
        self.assertFalse(is_locked_out(self.user))


# ── Password reset rate limit ────────────────────────────────────────────────

@override_settings(PASSWORD_RESET_LIMIT_PER_EMAIL=2, PASSWORD_RESET_LIMIT_PER_IP=100)
class PasswordResetRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        User.objects.create_user(username='sam', email='sam@example.com', password=PW)

    def tearDown(self):
        cache.clear()

    def _request(self, email='sam@example.com'):
        return self.client.post(reverse('password_reset'), {'email': email})

    def test_requests_over_the_limit_send_no_email(self):
        for _ in range(4):
            response = self._request()
            self.assertRedirects(response, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 2)

    def test_limit_ignores_case(self):
        self._request('sam@example.com')
        self._request('SAM@example.com')
        self._request('Sam@Example.com')
        self.assertEqual(len(mail.outbox), 2)

    @override_settings(PASSWORD_RESET_LIMIT_PER_EMAIL=100, PASSWORD_RESET_LIMIT_PER_IP=1)
    def test_ip_limit(self):
        User.objects.create_user(username='kit', email='kit@example.com', password=PW)
        self._request('sam@example.com')
        self._request('kit@example.com')
        self.assertEqual(len(mail.outbox), 1)


# ── Sessions ─────────────────────────────────────────────────────────────────

@override_settings(SESSION_ABSOLUTE_AGE=60 * 60)
class AbsoluteSessionLifetimeTests(TestCase):
    def setUp(self):
        self.user = make_admin('sessionuser')
        self.client.force_login(self.user)

    def _set_started(self, value):
        session = self.client.session
        if value is None:
            session.pop(SESSION_STARTED_KEY, None)
        else:
            session[SESSION_STARTED_KEY] = value
        session.save()

    def test_sign_in_records_the_start(self):
        self.assertIn(SESSION_STARTED_KEY, self.client.session)

    def test_session_inside_the_limit_works(self):
        self._set_started(int(time.time()) - 60)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)

    def test_session_past_the_limit_is_signed_out(self):
        self._set_started(int(time.time()) - 2 * 60 * 60)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_old_session_without_a_start_starts_counting_now(self):
        self._set_started(None)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)
        self.assertIn(SESSION_STARTED_KEY, self.client.session)


class SignOutEverywhereTests(TestCase):
    def setUp(self):
        self.admin = make_admin('admin2', email='admin2@example.com')
        self.user = make_admin('worker', email='worker@example.com')
        self.here, self.phone = Client(), Client()
        self.here.force_login(self.user)
        self.phone.force_login(self.user)

    def _signed_in(self, client):
        return client.get(reverse('dashboard')).status_code == 200

    def test_user_signs_out_other_devices_and_stays_here(self):
        self.here.post(reverse('sign_out_other_devices'))
        self.assertTrue(self._signed_in(self.here))
        self.assertFalse(self._signed_in(self.phone))

    def test_admin_signs_a_user_out_everywhere(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('user_update', args=[self.user.pk]), {'sign_out_everywhere': '1'})
        self.assertFalse(self._signed_in(self.here))
        self.assertFalse(self._signed_in(self.phone))
        self.assertTrue(self._signed_in(self.client))

    def test_other_users_sessions_are_kept(self):
        self.client.force_login(self.admin)
        self.here.post(reverse('sign_out_other_devices'))
        self.assertTrue(self._signed_in(self.client))


# ── Invitations ──────────────────────────────────────────────────────────────

class InviteTests(TestCase):
    def setUp(self):
        from core.models import Role
        self.admin = make_admin('inviter', email='inviter@example.com', first_name='Ada')
        self.role = Role.objects.create(name='Groom', access={'horses': 'view'})
        self.client.force_login(self.admin)

    def _create(self, **extra):
        data = {'first_name': 'New', 'last_name': 'Starter', 'email': 'new@example.com',
                'role': self.role.pk, 'password1': '', 'password2': ''}
        data.update(extra)
        return self.client.post(reverse('user_create'), data)

    def _invite_link(self):
        self.assertEqual(len(mail.outbox), 1)
        match = re.search(r'https?://[^/]+(/accounts/invite/\S+)', mail.outbox[0].body)
        self.assertIsNotNone(match, mail.outbox[0].body)
        return match.group(1)

    def test_blank_password_sends_an_invitation(self):
        self._create()
        user = User.objects.get(email='new@example.com')
        self.assertFalse(user.has_usable_password())
        self.assertEqual(mail.outbox[0].to, ['new@example.com'])
        self.assertIn('Ada', mail.outbox[0].body)
        self._invite_link()

    def test_invitation_link_sets_the_password_once(self):
        self._create()
        link = self._invite_link()
        guest = Client()
        response = guest.get(link)
        self.assertEqual(response.status_code, 302)
        form_page = guest.get(response.url)
        self.assertContains(form_page, 'Choose a password')
        new_pw = 'my-own-long-password'
        guest.post(response.url, {'new_password1': new_pw, 'new_password2': new_pw})
        self.assertTrue(User.objects.get(email='new@example.com').check_password(new_pw))
        # Used once: the same link no longer works.
        again = Client().get(link)
        self.assertContains(again, 'expired or was already used')

    def test_reset_token_is_not_an_invitation(self):
        self._create()
        user = User.objects.get(email='new@example.com')
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        reset_token = default_token_generator.make_token(user)
        self.assertFalse(invite_token_generator.check_token(user, reset_token))
        response = Client().get(reverse('invite_confirm', args=[uid, reset_token]))
        self.assertContains(response, 'expired or was already used')

    @override_settings(INVITE_TIMEOUT=60)
    def test_invitation_expires(self):
        from unittest import mock
        from datetime import datetime, timedelta

        self._create()
        user = User.objects.get(email='new@example.com')
        token = invite_token_generator.make_token(user)
        later = datetime.now() + timedelta(seconds=120)
        with mock.patch.object(invite_token_generator, '_now', return_value=later):
            self.assertFalse(invite_token_generator.check_token(user, token))

    def test_typed_password_still_works_and_sends_nothing(self):
        self._create(password1='a-chosen-long-password', password2='a-chosen-long-password')
        self.assertTrue(User.objects.get(email='new@example.com').check_password('a-chosen-long-password'))
        self.assertEqual(len(mail.outbox), 0)

    def test_confirm_without_password_is_an_error(self):
        response = self._create(password2='a-chosen-long-password')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email='new@example.com').exists())


# ── Two-step sign-in ─────────────────────────────────────────────────────────

class MFASetupTests(TestCase):
    def setUp(self):
        self.user = make_user_with_access('rider', dashboard='full')
        self.client.force_login(self.user)

    def test_setup_shows_a_qr_code_and_key(self):
        response = self.client.get(reverse('mfa_setup'))
        self.assertContains(response, '<svg')
        self.assertTrue(response.context['secret'])

    def test_setup_with_the_right_code_turns_it_on_and_shows_codes(self):
        self.client.get(reverse('mfa_setup'))
        device = mfa.pending_device(self.user, self.client.session['_mfa_pending_device'])
        response = self.client.post(reverse('mfa_setup'), {'code': totp_code(device)})
        self.assertTrue(mfa.has_mfa(self.user))
        self.assertEqual(len(response.context['codes']), mfa.RECOVERY_CODE_COUNT)
        # Still signed in and verified in this session.
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)

    def test_setup_with_a_wrong_code_does_not_turn_it_on(self):
        self.client.get(reverse('mfa_setup'))
        response = self.client.post(reverse('mfa_setup'), {'code': '000000'})
        self.assertContains(response, 'That code did not work')
        self.assertFalse(mfa.has_mfa(self.user))

    def test_setup_signs_out_other_devices(self):
        phone = Client()
        phone.force_login(self.user)
        self.client.get(reverse('mfa_setup'))
        device = mfa.pending_device(self.user, self.client.session['_mfa_pending_device'])
        self.client.post(reverse('mfa_setup'), {'code': totp_code(device)})
        self.assertEqual(phone.get(reverse('dashboard')).status_code, 302)

    def test_new_phone_replaces_the_old_one(self):
        old, _ = enrol(self.user)
        self.client.force_login(self.user)
        session = self.client.session
        session['otp_device_id'] = old.persistent_id
        session.save()
        self.client.get(reverse('mfa_setup'))
        new = mfa.pending_device(self.user, self.client.session['_mfa_pending_device'])
        self.client.post(reverse('mfa_setup'), {'code': totp_code(new, 1)})
        devices = list(mfa._totp_model().objects.filter(user=self.user))
        self.assertEqual([d.pk for d in devices], [new.pk])


class MFASignInTests(TestCase):
    def setUp(self):
        self.user = make_user_with_access('mfauser', dashboard='full')
        self.device, self.codes = enrol(self.user)
        self.client.force_login(self.user)

    def test_signed_in_user_is_held_at_the_code_page(self):
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('mfa_verify')))
        self.assertIn('next=', response.url)

    def test_htmx_requests_get_a_full_page_redirect(self):
        response = self.client.get(reverse('dashboard'), HTTP_HX_REQUEST='true')
        self.assertTrue(response['HX-Redirect'].startswith(reverse('mfa_verify')))

    def test_uploaded_files_and_admin_are_held_too(self):
        from django.conf import settings
        response = self.client.get('/' + settings.ADMIN_URL)
        self.assertTrue(response.url.startswith(reverse('mfa_verify')))

    def test_right_code_lets_the_user_in(self):
        response = self.client.post(
            reverse('mfa_verify'), {'code': totp_code(self.device, 1), 'next': reverse('finances')},
        )
        self.assertRedirects(response, reverse('finances'), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)

    def test_code_with_spaces_is_accepted(self):
        code = totp_code(self.device, 1)
        self.client.post(reverse('mfa_verify'), {'code': f'{code[:3]} {code[3:]}'})
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)

    def test_wrong_code_is_refused_with_help(self):
        response = self.client.post(reverse('mfa_verify'), {'code': '000000'})
        self.assertContains(response, 'recovery codes')
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 302)

    def test_recovery_code_works_once(self):
        code = self.codes[0]
        self.client.post(reverse('mfa_verify'), {'code': code.upper()})
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)
        self.assertEqual(mfa.recovery_codes_left(self.user), mfa.RECOVERY_CODE_COUNT - 1)

        other = Client()
        other.force_login(self.user)
        other.post(reverse('mfa_verify'), {'code': code})
        self.assertEqual(other.get(reverse('dashboard')).status_code, 302)

    def test_unsafe_next_is_ignored(self):
        response = self.client.post(
            reverse('mfa_verify'), {'code': totp_code(self.device, 1), 'next': 'https://evil.example/'},
        )
        self.assertEqual(response.url, reverse('dashboard'))

    def test_sign_out_is_allowed_before_the_code(self):
        response = self.client.post(reverse('logout'))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_new_recovery_codes_replace_old(self):
        self.client.post(reverse('mfa_verify'), {'code': totp_code(self.device, 1)})
        response = self.client.post(reverse('mfa_new_recovery_codes'))
        new_codes = response.context['codes']
        self.assertEqual(len(new_codes), mfa.RECOVERY_CODE_COUNT)
        # New first: a wrong code adds to the throttle.
        self.assertIsNotNone(mfa.verify(self.user, new_codes[0]))
        self.assertIsNone(mfa.verify(self.user, self.codes[1]))

    def test_turning_off_needs_a_code(self):
        self.client.post(reverse('mfa_verify'), {'code': totp_code(self.device, 1)})
        self.client.post(reverse('mfa_disable'), {'code': '000000'})
        self.assertTrue(mfa.has_mfa(self.user))
        self.client.post(reverse('mfa_disable'), {'code': self.codes[2]})
        self.assertFalse(mfa.has_mfa(self.user))
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)

    def test_turned_off_elsewhere_does_not_loop(self):
        mfa.disable(self.user)
        response = self.client.get(reverse('dashboard'), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.request['PATH_INFO'], reverse('dashboard'))


@override_settings(MFA_REQUIRED_FOR_ADMINS=True)
class MFARequiredForAdminsTests(TestCase):
    def test_administrator_without_mfa_is_sent_to_setup(self):
        admin = make_admin('needsmfa')
        self.client.force_login(admin)
        response = self.client.get(reverse('dashboard'))
        self.assertTrue(response.url.startswith(reverse('mfa_setup')))
        page = self.client.get(response.url)
        self.assertContains(page, 'needs a code from your phone')

    def test_other_roles_are_not_forced(self):
        user = make_user_with_access('groom', dashboard='full')
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)

    def test_administrator_cannot_turn_it_off(self):
        admin = make_admin('keepson')
        device, codes = enrol(admin)
        self.client.force_login(admin)
        self.client.post(reverse('mfa_verify'), {'code': totp_code(device, 1)})
        self.client.post(reverse('mfa_disable'), {'code': codes[0]})
        self.assertTrue(mfa.has_mfa(admin))

    @override_settings(MFA_REQUIRED_FOR_ADMINS=False)
    def test_setting_switches_the_requirement_off(self):
        admin = make_admin('emergency')
        self.client.force_login(admin)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)


class AdminMFAResetTests(TestCase):
    """For the user who lost their phone and their recovery codes."""

    def setUp(self):
        self.target = make_user_with_access('lostphone', dashboard='full')
        enrol(self.target)
        self.target_client = Client()
        self.target_client.force_login(self.target)

    def test_administrator_turns_it_off_and_signs_them_out(self):
        admin = make_admin('resetter')
        self.client.force_login(admin)
        response = self.client.get(reverse('user_update', args=[self.target.pk]))
        self.assertContains(response, 'name="mfa_reset"')
        self.client.post(reverse('user_update', args=[self.target.pk]), {'mfa_reset': '1'})
        self.assertFalse(mfa.has_mfa(self.target))
        self.assertEqual(self.target_client.get(reverse('dashboard')).status_code, 302)

    def test_user_manager_who_is_not_an_administrator_cannot(self):
        manager = make_user_with_access('manager', users='full', settings='full')
        self.client.force_login(manager)
        response = self.client.get(reverse('user_update', args=[self.target.pk]))
        self.assertNotContains(response, 'name="mfa_reset"')
        self.client.post(reverse('user_update', args=[self.target.pk]), {'mfa_reset': '1'})
        self.assertTrue(mfa.has_mfa(self.target))

    def test_management_command(self):
        out = StringIO()
        call_command('mfa_reset', 'lostphone', stdout=out)
        self.assertFalse(mfa.has_mfa(self.target))
        self.assertIn('Two-step sign-in is off', out.getvalue())


class AccountSecurityPageTests(TestCase):
    def test_page_shows_status_and_actions(self):
        user = make_user_with_access('pageuser', dashboard='full')
        self.client.force_login(user)
        response = self.client.get(reverse('account_security'))
        self.assertContains(response, 'Two-Step Sign-In')
        self.assertContains(response, reverse('mfa_setup'))
        self.assertContains(response, reverse('sign_out_other_devices'))
        self.assertContains(response, reverse('password_change'))
