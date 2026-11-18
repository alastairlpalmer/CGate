"""The profile pop-up (core.views.profile).

docs/SIDEBAR_AND_NOTIFICATIONS_PLAN.md, WP2.
"""

import io
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from core.models import AuditEvent, UserProfile
from core.roles_testutils import make_admin, make_user_with_access

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}


def _jpeg(name='me.jpg', size=(400, 300)):
    buffer = io.BytesIO()
    Image.new('RGB', size, '#A0522D').save(buffer, format='JPEG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/jpeg')


def _details(**overrides):
    data = {
        'action': 'details', 'first_name': 'Anita', 'last_name': 'Cruz',
        'email': 'anita@example.com', 'phone': '07700 900123', 'display_size': 'compact',
    }
    data.update(overrides)
    return data


class ProfileTestBase(TestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media = tempfile.mkdtemp()
        cls._override = override_settings(MEDIA_ROOT=cls._media)
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        shutil.rmtree(cls._media, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = make_user_with_access('rider', horses='view')
        self.user.email = 'rider@example.com'
        self.user.save()
        self.client.force_login(self.user)
        self.url = reverse('profile')


class ProfilePageTests(ProfileTestBase):

    def test_every_signed_in_user_can_open_it(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Your Profile')

    def test_signed_out_user_is_sent_to_sign_in(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])

    def test_popup_renders_only_the_body(self):
        response = self.client.get(self.url, **POPUP)
        self.assertNotContains(response, '<html')
        self.assertContains(response, 'role="tablist"')
        self.assertContains(response, 'hx-target="#popup-body"')

    def test_security_tab_can_open_first(self):
        response = self.client.get(self.url + '?tab=security', **POPUP)
        self.assertContains(response, "x-data=\"{ tab: 'security' }\"")

    def test_sidebar_and_more_sheet_open_the_popup(self):
        html = self.client.get(reverse('horse_list')).content.decode()
        self.assertEqual(html.count(f'hx-get="{self.url}"'), 2)
        self.assertIn('data-popup-title="Your profile"', html)


class ProfileDetailsTests(ProfileTestBase):

    def test_saves_name_email_and_phone(self):
        response = self.client.post(self.url, _details(), **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response['HX-Trigger'], 'popup:saved')
        self.assertEqual(response['HX-Refresh'], 'true')
        self.user.refresh_from_db()
        self.assertEqual(self.user.get_full_name(), 'Anita Cruz')
        self.assertEqual(self.user.email, 'anita@example.com')
        self.assertEqual(UserProfile.objects.get(user=self.user).phone, '07700 900123')

    def test_full_page_save_redirects(self):
        response = self.client.post(self.url, _details())
        self.assertRedirects(response, self.url)

    def test_email_taken_by_someone_else_is_refused(self):
        get_user_model().objects.create_user('other', email='Taken@Example.com', password='x')
        response = self.client.post(self.url, _details(email='taken@example.com'), **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Another user already has this email address.')
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'rider@example.com')

    def test_keeping_your_own_email_is_allowed(self):
        response = self.client.post(self.url, _details(email='RIDER@example.com'), **POPUP)
        self.assertEqual(response.status_code, 204)

    def test_email_change_is_audited(self):
        self.client.post(self.url, _details(), **POPUP)
        self.assertTrue(AuditEvent.objects.filter(
            action=AuditEvent.Action.USER_UPDATED, target_user=self.user,
        ).exists())

    def test_no_audit_when_email_is_unchanged(self):
        self.client.post(self.url, _details(email='rider@example.com'), **POPUP)
        self.assertFalse(AuditEvent.objects.filter(action=AuditEvent.Action.USER_UPDATED).exists())

    def test_photo_is_stored_as_a_small_square(self):
        self.client.post(self.url, {**_details(), 'photo': _jpeg()}, **POPUP)
        profile = UserProfile.objects.get(user=self.user)
        self.assertTrue(profile.photo)
        with Image.open(profile.photo.path) as image:
            self.assertEqual(image.size, (320, 320))

    def test_photo_shows_in_the_sidebar(self):
        self.client.post(self.url, {**_details(), 'photo': _jpeg()}, **POPUP)
        html = self.client.get(reverse('horse_list')).content.decode()
        photo_url = UserProfile.objects.get(user=self.user).photo.url
        self.assertIn(f'<img src="{photo_url}"', html)

    def test_remove_photo(self):
        self.client.post(self.url, {**_details(), 'photo': _jpeg()}, **POPUP)
        self.client.post(self.url, _details(remove_photo='on'), **POPUP)
        self.assertFalse(UserProfile.objects.get(user=self.user).photo)

    def test_a_file_that_is_not_a_photo_is_refused(self):
        fake = SimpleUploadedFile('me.jpg', b'not an image at all', content_type='image/jpeg')
        response = self.client.post(self.url, {**_details(), 'photo': fake}, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'not a photo we can read')
        self.assertFalse(UserProfile.objects.get(user=self.user).photo)

    def test_a_user_changes_only_their_own_profile(self):
        other = make_admin('boss', email='boss@example.com')
        self.client.post(self.url, _details(first_name='Changed'), **POPUP)
        other.refresh_from_db()
        self.assertEqual(other.first_name, '')


class ProfilePasswordTests(ProfileTestBase):

    def test_change_password(self):
        response = self.client.post(self.url, {
            'action': 'password', 'old_password': 'pw',
            'new_password1': 'a-much-better-one-42', 'new_password2': 'a-much-better-one-42',
        }, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('a-much-better-one-42'))
        self.assertTrue(AuditEvent.objects.filter(
            action=AuditEvent.Action.PASSWORD_CHANGED, target_user=self.user,
        ).exists())
        # This session stays signed in.
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_wrong_current_password_is_refused(self):
        response = self.client.post(self.url, {
            'action': 'password', 'old_password': 'wrong',
            'new_password1': 'a-much-better-one-42', 'new_password2': 'a-much-better-one-42',
        }, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "x-data=\"{ tab: 'security' }\"")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('pw'))

    def test_google_only_user_sees_no_password_form(self):
        self.user.set_unusable_password()
        self.user.save()
        self.client.force_login(self.user)
        response = self.client.get(self.url, **POPUP)
        self.assertNotContains(response, 'name="old_password"')
        self.assertContains(response, 'have no password')
