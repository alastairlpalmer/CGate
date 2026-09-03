"""Tests for the quick-add photo form inside the pop-up sheet, the
"use as profile picture" options, and the profile-picture label."""

import io
import shutil
import tempfile
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Document, Horse, HorsePhoto, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin, make_viewer

TEMP_MEDIA = tempfile.mkdtemp(prefix='cgate-photo-popup-tests-')

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}


def _image_bytes(size=(64, 64), color='red'):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new('RGB', size, color).save(buffer, format='JPEG')
    return buffer.getvalue()


def _photo(name='snap.jpg'):
    return SimpleUploadedFile(name, _image_bytes(), content_type='image/jpeg')


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class PhotoPopupTestCase(TestCase):

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.horse = Horse.objects.create(name='Dobbin')
        self.url = reverse('horse_photo_add', args=[self.horse.pk])
        self.client.force_login(make_admin())


class PhotoPopupGetTests(PhotoPopupTestCase):

    def test_popup_request_gets_the_form_only(self):
        response = self.client.get(self.url, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'horses/partials/photo_form.html')
        self.assertNotContains(response, '<html')
        self.assertContains(response, f'hx-post="{self.url}"')
        self.assertContains(response, 'hx-encoding="multipart/form-data"')
        self.assertContains(response, 'hx-target="#popup-body"')
        self.assertContains(response, 'popup-footer')
        self.assertContains(response, 'Also use as profile picture')

    def test_popup_honours_category_preselect(self):
        response = self.client.get(self.url + '?category=arrival', **POPUP)
        self.assertEqual(response.context['form']['category'].value(), 'arrival')
        self.assertContains(response, "quickPhotoAdd('arrival')")

    def test_plain_request_gets_the_full_page(self):
        response = self.client.get(self.url)
        self.assertTemplateUsed(response, 'horses/photo_quick_add.html')
        self.assertContains(response, '<html')
        self.assertContains(response, 'form-footer')
        self.assertNotContains(response, 'hx-post=')


class PhotoPopupPostTests(PhotoPopupTestCase):

    def test_valid_save_answers_204_with_trigger(self):
        response = self.client.post(self.url, {
            'category': 'condition',
            'images': [_photo('a.jpg'), _photo('b.jpg')],
        }, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response['HX-Trigger'], 'popup:saved')
        self.assertEqual(HorsePhoto.objects.filter(horse=self.horse).count(), 2)
        self.horse.refresh_from_db()
        self.assertFalse(self.horse.photo)  # not asked for

    def test_nothing_saved_re_renders_with_inline_errors(self):
        bad = SimpleUploadedFile('doc.txt', b'plain text', content_type='text/plain')
        response = self.client.post(self.url, {
            'category': 'condition',
            'images': [bad],
        }, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'horses/partials/photo_form.html')
        self.assertNotContains(response, '<html')
        self.assertContains(response, 'Nothing was saved')
        self.assertContains(response, 'doc.txt')
        self.assertNotIn('HX-Trigger', response)
        self.assertEqual(HorsePhoto.objects.count(), 0)

    def test_set_profile_copies_first_photo_to_avatar(self):
        response = self.client.post(self.url, {
            'category': 'markings',
            'images': [_photo('first.jpg'), _photo('second.jpg')],
            'set_profile': 'on',
        }, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.horse.refresh_from_db()
        self.assertTrue(self.horse.photo)
        self.assertIn('first', self.horse.photo.name)
        self.assertTrue(self.horse.photo_thumb, 'avatar thumbnail should be generated')
        # The log entry keeps its own file
        self.assertEqual(HorsePhoto.objects.filter(horse=self.horse).count(), 2)
        follow_up = self.client.get(reverse('horse_detail', args=[self.horse.pk]))
        texts = [str(m) for m in follow_up.context['messages']]
        self.assertTrue(any('Profile picture updated' in m for m in texts), texts)

    def test_set_profile_ignored_for_passport(self):
        response = self.client.post(self.url, {
            'category': 'passport',
            'images': [_photo('front.jpg')],
            'set_profile': 'on',
        }, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(Document.objects.filter(horse=self.horse, doc_type='passport').count(), 1)
        self.assertEqual(HorsePhoto.objects.count(), 0)
        self.horse.refresh_from_db()
        self.assertFalse(self.horse.photo)

    def test_set_profile_works_on_the_full_page_too(self):
        response = self.client.post(self.url, {
            'category': 'condition',
            'images': [_photo('only.jpg')],
            'set_profile': 'on',
        })
        self.assertRedirects(response, reverse('horse_detail', args=[self.horse.pk]))
        self.horse.refresh_from_db()
        self.assertTrue(self.horse.photo)


class UseAsProfileViewTests(PhotoPopupTestCase):

    def setUp(self):
        super().setUp()
        self.photo = HorsePhoto.objects.create(
            horse=self.horse, image=_photo('legs.jpg'), category='condition'
        )
        self.set_url = reverse('horse_photo_set_profile', args=[self.photo.pk])

    def test_post_sets_profile_picture(self):
        response = self.client.post(self.set_url)
        self.assertRedirects(response, reverse('horse_detail', args=[self.horse.pk]))
        self.horse.refresh_from_db()
        self.assertTrue(self.horse.photo)
        self.assertIn('legs', self.horse.photo.name)
        self.assertTrue(self.horse.photo_thumb)
        self.assertEqual(HorsePhoto.objects.count(), 1)

    def test_get_is_a_noop_redirect(self):
        response = self.client.get(self.set_url)
        self.assertRedirects(response, reverse('horse_detail', args=[self.horse.pk]))
        self.horse.refresh_from_db()
        self.assertFalse(self.horse.photo)

    def test_viewer_is_refused(self):
        self.client.force_login(make_viewer())
        self.assertEqual(self.client.post(self.set_url).status_code, 403)
        self.assertEqual(self.client.get(self.url, **POPUP).status_code, 403)
        self.horse.refresh_from_db()
        self.assertFalse(self.horse.photo)

    def test_grid_card_offers_use_as_profile(self):
        response = self.client.get(reverse('horse_detail', args=[self.horse.pk]))
        self.assertContains(response, f'action="{self.set_url}"')
        self.assertContains(response, 'Use as profile picture')


class PhotoTriggerTests(PhotoPopupTestCase):

    def setUp(self):
        super().setUp()
        owner = Owner.objects.create(name='Jo Bloggs')
        self.location = Location.objects.create(name='Top Field', site='Main')
        rate = RateType.objects.create(name='Full', daily_rate=10)
        Placement.objects.create(
            horse=self.horse, owner=owner, location=self.location, rate_type=rate,
            start_date=timezone.localdate() - timedelta(days=3),
        )

    def test_photo_triggers_open_the_sheet(self):
        for name, args in (
            ('horse_list', []),
            ('horse_detail', [self.horse.pk]),
            ('location_detail', [self.location.pk]),
        ):
            with self.subTest(page=name):
                response = self.client.get(reverse(name, args=args))
                self.assertContains(response, 'data-popup-title="Add photos for Dobbin"')
                self.assertContains(response, f'hx-get="{self.url}')
                self.assertContains(response, f'href="{self.url}')

    def test_horse_detail_has_three_photo_entry_points(self):
        # Header button, Photos card "+ Add", Quick Actions tile
        response = self.client.get(reverse('horse_detail', args=[self.horse.pk]))
        self.assertContains(response, 'data-popup-title="Add photos for Dobbin"', count=3)

    def test_location_detail_keeps_arrival_preselect(self):
        response = self.client.get(reverse('location_detail', args=[self.location.pk]))
        self.assertContains(response, f'hx-get="{self.url}?category=arrival"')

    def test_edit_page_calls_the_avatar_a_profile_picture(self):
        response = self.client.get(reverse('horse_update', args=[self.horse.pk]))
        self.assertContains(response, 'Profile picture')
        self.assertContains(response, 'Photos log')

    def test_photo_component_script_is_loaded(self):
        response = self.client.get(reverse('horse_list'))
        self.assertContains(response, 'js/photo_quick_add.js')
