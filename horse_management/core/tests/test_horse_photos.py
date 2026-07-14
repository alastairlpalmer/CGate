"""Tests for the quick-add horse photo flow (HorsePhoto + views)."""

import io
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from core.images import normalise_photo
from core.models import Document, Horse, HorsePhoto
from core.roles_testutils import make_admin, make_viewer

TEMP_MEDIA = tempfile.mkdtemp(prefix='cgate-photo-tests-')


def _image_bytes(fmt='JPEG', size=(64, 64), color='red'):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new('RGB', size, color).save(buffer, format=fmt)
    return buffer.getvalue()


def _photo(name='snap.jpg', fmt='JPEG', size=(64, 64)):
    return SimpleUploadedFile(name, _image_bytes(fmt, size), content_type='image/jpeg')


class NormalisePhotoTests(TestCase):

    def test_heic_converted_to_jpeg(self):
        upload = SimpleUploadedFile(
            'IMG_1.heic', _image_bytes('HEIF'), content_type='image/heic'
        )
        converted = normalise_photo(upload)
        self.assertEqual(converted.name, 'IMG_1.jpg')
        self.assertEqual(converted.content_type, 'image/jpeg')

    def test_oversized_jpeg_downscaled(self):
        from PIL import Image

        upload = _photo('big.jpg', size=(4000, 3000))
        image = Image.open(normalise_photo(upload))
        self.assertLessEqual(max(image.size), 2560)
        self.assertEqual(image.format, 'JPEG')

    def test_small_jpeg_passes_through_unchanged(self):
        upload = _photo('small.jpg')
        self.assertIs(normalise_photo(upload), upload)

    def test_none_and_false_pass_through(self):
        self.assertIsNone(normalise_photo(None))
        self.assertIs(normalise_photo(False), False)


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class HorsePhotoModelTests(TestCase):

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.horse = Horse.objects.create(name='Dobbin')

    def test_thumb_generated_on_save(self):
        from PIL import Image

        photo = HorsePhoto.objects.create(
            horse=self.horse, image=_photo(size=(1600, 1200))
        )
        photo.refresh_from_db()
        self.assertTrue(photo.thumb)
        with photo.thumb.open('rb') as fh:
            image = Image.open(fh)
            self.assertEqual(image.size, (480, 480))

    def test_corrupt_image_still_saves_without_thumb(self):
        corrupt = SimpleUploadedFile(
            'broken.jpg', b'not really a jpeg', content_type='image/jpeg'
        )
        photo = HorsePhoto.objects.create(horse=self.horse, image=corrupt)
        photo.refresh_from_db()
        self.assertFalse(photo.thumb)


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class QuickAddViewTests(TestCase):

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.staff = make_admin()
        self.horse = Horse.objects.create(name='Dobbin')
        self.url = reverse('horse_photo_add', args=[self.horse.pk])
        self.client.login(username='admin', password='pw')

    def test_get_renders_form_with_category_preselect(self):
        resp = self.client.get(self.url + '?category=arrival')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['form']['category'].value(), 'arrival')

    def test_get_ignores_unknown_category(self):
        resp = self.client.get(self.url + '?category=selfie')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['form']['category'].value(), 'condition')

    def test_multiple_photos_saved(self):
        resp = self.client.post(self.url, {
            'category': 'condition',
            'caption': 'Nearside legs',
            'images': [_photo('a.jpg'), _photo('b.jpg'), _photo('c.jpg')],
        })
        self.assertRedirects(resp, reverse('horse_detail', args=[self.horse.pk]))
        photos = HorsePhoto.objects.filter(horse=self.horse)
        self.assertEqual(photos.count(), 3)
        photo = photos.first()
        self.assertEqual(photo.category, 'condition')
        self.assertEqual(photo.caption, 'Nearside legs')
        self.assertEqual(photo.uploaded_by, self.staff)

    def test_oversized_photo_downscaled_not_rejected(self):
        # A full-res phone JPEG > 5MB must be normalised, not bounced.
        resp = self.client.post(self.url, {
            'category': 'markings',
            'images': [_photo('huge.jpg', size=(6000, 4000))],
        })
        self.assertRedirects(resp, reverse('horse_detail', args=[self.horse.pk]))
        self.assertEqual(HorsePhoto.objects.filter(horse=self.horse).count(), 1)

    def test_partial_success_skips_bad_file(self):
        bad = SimpleUploadedFile('doc.txt', b'plain text', content_type='text/plain')
        resp = self.client.post(self.url, {
            'category': 'condition',
            'images': [_photo('good.jpg'), bad],
        }, follow=True)
        self.assertEqual(HorsePhoto.objects.filter(horse=self.horse).count(), 1)
        text = [str(m) for m in resp.context['messages']]
        self.assertTrue(any('Skipped doc.txt' in m for m in text))
        self.assertTrue(any('1 photo saved' in m for m in text))

    def test_all_invalid_redisplays_form(self):
        bad = SimpleUploadedFile('doc.txt', b'plain text', content_type='text/plain')
        resp = self.client.post(self.url, {
            'category': 'condition',
            'images': [bad],
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(HorsePhoto.objects.count(), 0)

    def test_passport_routes_to_document(self):
        resp = self.client.post(self.url, {
            'category': 'passport',
            'images': [_photo('front.jpg'), _photo('back.jpg')],
        })
        self.assertRedirects(resp, reverse('horse_detail', args=[self.horse.pk]))
        self.assertEqual(HorsePhoto.objects.count(), 0)
        docs = Document.objects.filter(horse=self.horse, doc_type='passport')
        self.assertEqual(docs.count(), 2)
        titles = set(docs.values_list('title', flat=True))
        self.assertEqual(len(titles), 2)  # distinct auto-titles within the batch
        for title in titles:
            self.assertIn('Passport photo', title)

    def test_photo_grid_renders_on_horse_detail(self):
        HorsePhoto.objects.create(horse=self.horse, image=_photo(), category='injury')
        resp = self.client.get(reverse('horse_detail', args=[self.horse.pk]))
        self.assertContains(resp, 'Photos')
        self.assertContains(resp, 'horse_photos/')


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class PhotoDeleteTests(TestCase):

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.staff = make_admin()
        self.horse = Horse.objects.create(name='Dobbin')
        self.photo = HorsePhoto.objects.create(horse=self.horse, image=_photo())
        self.url = reverse('horse_photo_delete', args=[self.photo.pk])
        self.client.login(username='admin', password='pw')

    def test_post_deletes_photo_and_files(self):
        storage = self.photo.image.storage
        image_name = self.photo.image.name
        thumb_name = self.photo.thumb.name
        resp = self.client.post(self.url)
        self.assertRedirects(resp, reverse('horse_detail', args=[self.horse.pk]))
        self.assertEqual(HorsePhoto.objects.count(), 0)
        self.assertFalse(storage.exists(image_name))
        self.assertFalse(storage.exists(thumb_name))

    def test_get_is_noop_redirect(self):
        resp = self.client.get(self.url)
        self.assertRedirects(resp, reverse('horse_detail', args=[self.horse.pk]))
        self.assertEqual(HorsePhoto.objects.count(), 1)


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class PhotoGatingTests(TestCase):

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.horse = Horse.objects.create(name='Dobbin')

    def test_logged_out_redirected_to_login(self):
        resp = self.client.get(reverse('horse_photo_add', args=[self.horse.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/accounts/login/', resp['Location'])

    def test_view_only_user_cannot_add_or_delete(self):
        # Viewer role: 'horses' at view level — not the full access photos need.
        make_viewer()
        self.client.login(username='viewer', password='pw')
        # Plain GET denial redirects to the dashboard with a message.
        self.assertRedirects(
            self.client.get(reverse('horse_photo_add', args=[self.horse.pk])),
            reverse('dashboard'),
        )
        self.assertEqual(HorsePhoto.objects.count(), 0)
        photo = HorsePhoto.objects.create(horse=self.horse, image=_photo())
        # POST denial is a hard 403.
        self.assertEqual(
            self.client.post(reverse('horse_photo_delete', args=[photo.pk])).status_code,
            403,
        )
        self.assertEqual(HorsePhoto.objects.count(), 1)

    def test_viewer_still_sees_photo_grid(self):
        make_viewer()
        HorsePhoto.objects.create(horse=self.horse, image=_photo())
        self.client.login(username='viewer', password='pw')
        resp = self.client.get(reverse('horse_detail', args=[self.horse.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'horse_photos/')
