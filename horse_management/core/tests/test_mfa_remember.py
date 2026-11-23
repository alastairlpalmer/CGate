"""Two-step sign-in: "Remember this device".

After a correct code, a device can be remembered for MFA_REMEMBER_DAYS
(14). The next sign-in on it asks for the password only. These tests hold
what ends that early: time, a new password, a new or removed
authenticator, and "sign out everywhere".
"""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core import mfa
from core.account_security import sign_out_everywhere
from core.models import AuditEvent
from core.roles_testutils import make_user_with_access
from core.tests.test_account_security import PW, enrol, totp_code

User = get_user_model()
DAY = 24 * 60 * 60


class RememberDeviceTests(TestCase):
    def setUp(self):
        self.user = make_user_with_access('yardboss', password=PW, dashboard='full')
        self.device, self.codes = enrol(self.user)
        self.step = 0

    # ── helpers ──

    def sign_in(self, client=None, password=PW, username='yardboss'):
        client = client or self.client
        return client.post(reverse('login'), {'username': username, 'password': password})

    def sign_out(self, client=None):
        (client or self.client).post(reverse('logout'))

    def give_code(self, remember=True, client=None, code=None):
        """Post a correct code. Each call uses a new one: a code works once."""
        self.step += 1
        data = {'code': code or totp_code(self.device, self.step)}
        if remember:
            data['remember'] = '1'
        return (client or self.client).post(reverse('mfa_verify'), data)

    def asked_for_a_code(self, client=None):
        response = (client or self.client).get(reverse('dashboard'))
        if response.status_code == 302:
            self.assertTrue(response.url.startswith(reverse('mfa_verify')), response.url)
            return True
        self.assertEqual(response.status_code, 200)
        return False

    def remembered_then_signed_out(self):
        self.sign_in()
        self.give_code(remember=True)
        self.sign_out()

    # ── the code page ──

    def test_the_code_page_offers_to_remember_the_device_and_the_box_is_ticked(self):
        self.sign_in()
        response = self.client.get(reverse('mfa_verify'))
        self.assertContains(response, 'Remember this device for 14 days')
        self.assertContains(response, '<input type="checkbox" name="remember" value="1" class="form-checkbox shrink-0" checked>', html=True)

    def test_a_wrong_code_keeps_the_box_as_the_person_left_it(self):
        self.sign_in()
        response = self.client.post(reverse('mfa_verify'), {'code': '000000'})
        self.assertContains(response, 'That code did not work')
        self.assertNotContains(response, 'checked>')

    # ── remembering ──

    def test_a_correct_code_with_the_box_ticked_puts_a_cookie_on_the_device(self):
        self.sign_in()
        response = self.give_code(remember=True)
        cookie = response.cookies[mfa.REMEMBER_COOKIE]
        self.assertTrue(cookie.value)
        self.assertEqual(cookie['max-age'], 14 * DAY)
        self.assertTrue(cookie['httponly'])
        self.assertEqual(cookie['samesite'], 'Lax')
        event = AuditEvent.objects.get(action=AuditEvent.Action.MFA_DEVICE_REMEMBERED)
        self.assertEqual(event.detail, '14 days')

    @override_settings(SESSION_COOKIE_SECURE=True)
    def test_the_cookie_is_secure_when_the_session_cookie_is(self):
        self.sign_in()
        self.assertTrue(self.give_code(remember=True).cookies[mfa.REMEMBER_COOKIE]['secure'])

    def test_the_cookie_does_not_hold_the_password_or_the_authenticator_key(self):
        self.sign_in()
        value = self.give_code(remember=True).cookies[mfa.REMEMBER_COOKIE].value
        self.assertNotIn(self.device.key, value)
        self.assertNotIn(self.user.password, value)

    def test_the_next_sign_in_on_the_device_asks_for_the_password_only(self):
        self.remembered_then_signed_out()
        self.sign_in()
        self.assertFalse(self.asked_for_a_code())
        self.assertTrue(AuditEvent.objects.filter(action=AuditEvent.Action.MFA_CODE_SKIPPED).exists())

    def test_the_password_is_still_needed(self):
        self.remembered_then_signed_out()
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('login')))
        self.sign_in(password='not-the-password')
        self.assertTrue(self.client.get(reverse('dashboard')).url.startswith(reverse('login')))

    def test_the_code_page_itself_lets_a_remembered_device_through(self):
        self.remembered_then_signed_out()
        self.sign_in()
        response = self.client.get(reverse('mfa_verify') + '?next=/horses/')
        self.assertRedirects(response, '/horses/', fetch_redirect_response=False)

    def test_a_recovery_code_can_remember_the_device_too(self):
        self.sign_in()
        self.give_code(remember=True, code=self.codes[0])
        self.sign_out()
        self.sign_in()
        self.assertFalse(self.asked_for_a_code())

    # ── not remembering ──

    def test_without_the_box_the_next_sign_in_asks_for_a_code(self):
        self.sign_in()
        response = self.give_code(remember=False)
        self.assertNotIn(mfa.REMEMBER_COOKIE, [name for name, c in response.cookies.items() if c.value])
        self.sign_out()
        self.sign_in()
        self.assertTrue(self.asked_for_a_code())

    def test_without_the_box_a_device_that_was_remembered_is_forgotten(self):
        self.remembered_then_signed_out()
        # Someone takes the cookie away and gives a code with the box clear.
        del self.client.cookies[mfa.REMEMBER_COOKIE]
        self.sign_in()
        response = self.give_code(remember=False)
        self.assertEqual(response.cookies[mfa.REMEMBER_COOKIE].value, '')

    def test_another_browser_is_asked_for_a_code(self):
        self.remembered_then_signed_out()
        other = Client()
        self.sign_in(other)
        self.assertTrue(self.asked_for_a_code(other))

    # ── what ends it ──

    def test_after_14_days_the_device_is_asked_for_a_code_again(self):
        self.remembered_then_signed_out()
        with mock.patch('django.core.signing.time.time', return_value=__import__('time').time() + 14 * DAY + 60):
            self.sign_in()
            self.assertTrue(self.asked_for_a_code())

    def test_on_day_13_it_is_still_remembered(self):
        self.remembered_then_signed_out()
        with mock.patch('django.core.signing.time.time', return_value=__import__('time').time() + 13 * DAY):
            self.sign_in()
            self.assertFalse(self.asked_for_a_code())

    def test_a_new_password_ends_it(self):
        self.remembered_then_signed_out()
        self.user.set_password('a-new-correct-horse-staple')
        self.user.save()
        self.sign_in(password='a-new-correct-horse-staple')
        self.assertTrue(self.asked_for_a_code())

    def test_a_new_phone_ends_it(self):
        self.remembered_then_signed_out()
        mfa.disable(self.user)
        self.device, self.codes = enrol(self.user)
        self.sign_in()
        self.assertTrue(self.asked_for_a_code())

    def test_sign_out_everywhere_ends_it(self):
        self.remembered_then_signed_out()
        sign_out_everywhere(self.user)
        self.sign_in()
        self.assertTrue(self.asked_for_a_code())

    def test_sign_out_other_devices_from_the_app_ends_it_for_every_device(self):
        self.sign_in()
        self.give_code(remember=True)
        self.client.post(reverse('sign_out_other_devices'))
        self.sign_out()
        self.sign_in()
        self.assertTrue(self.asked_for_a_code())

    def test_a_device_remembered_after_sign_out_everywhere_works(self):
        sign_out_everywhere(self.user)
        self.remembered_then_signed_out()
        self.sign_in()
        self.assertFalse(self.asked_for_a_code())

    def test_a_cookie_for_someone_else_is_not_used(self):
        self.remembered_then_signed_out()
        other = make_user_with_access('secondboss', password=PW, dashboard='full')
        enrol(other)
        self.sign_in(username='secondboss')
        self.assertTrue(self.asked_for_a_code())

    def test_a_changed_cookie_is_not_used(self):
        self.remembered_then_signed_out()
        value = self.client.cookies[mfa.REMEMBER_COOKIE].value
        self.client.cookies[mfa.REMEMBER_COOKIE] = value[:-2] + ('aa' if not value.endswith('aa') else 'bb')
        self.sign_in()
        self.assertTrue(self.asked_for_a_code())

    # ── the setting ──

    @override_settings(MFA_REMEMBER_DAYS=0)
    def test_zero_days_turns_it_off(self):
        self.sign_in()
        self.assertNotContains(self.client.get(reverse('mfa_verify')), 'name="remember"')
        response = self.give_code(remember=True)
        self.assertFalse(response.cookies[mfa.REMEMBER_COOKIE].value if mfa.REMEMBER_COOKIE in response.cookies else '')

    def test_zero_days_ignores_a_cookie_made_before(self):
        self.remembered_then_signed_out()
        with override_settings(MFA_REMEMBER_DAYS=0):
            self.sign_in()
            self.assertTrue(self.asked_for_a_code())

    @override_settings(MFA_REMEMBER_DAYS=30)
    def test_the_number_of_days_is_a_setting(self):
        self.sign_in()
        self.assertContains(self.client.get(reverse('mfa_verify')), 'Remember this device for 30 days')
        self.assertEqual(self.give_code(remember=True).cookies[mfa.REMEMBER_COOKIE]['max-age'], 30 * DAY)

    # ── the account security page ──

    def test_the_account_security_page_says_how_long_and_how_to_end_it(self):
        self.sign_in()
        self.give_code(remember=False)
        response = self.client.get(reverse('account_security'))
        self.assertContains(response, 'A device can be remembered for 14 days after a correct code.')
        self.assertContains(response, 'Every device then asks for a two-step code again.')
