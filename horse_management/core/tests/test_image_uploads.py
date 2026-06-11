"""Tests for photo upload handling.

iPhones upload photos as HEIC by default. These uploads must validate
(pillow-heif opener registered in CoreConfig.ready, extension allowed on
the model fields) and be converted to JPEG (core.images.heic_to_jpeg) so
browsers can display them. Saving must also work end-to-end — a missing
'default' key in STORAGES once made every upload save crash with
InvalidStorageError.
"""

import io
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from core.forms import HorseForm
from core.images import heic_to_jpeg
from core.models import Horse


def _make_image_bytes(fmt, size=(32, 32)):
    img = Image.new('RGB', size, 'red')
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def _heic_upload(name='photo.heic'):
    return SimpleUploadedFile(name, _make_image_bytes('HEIF'), content_type='image/heic')


class HeicToJpegTests(TestCase):
    def test_heic_converted_to_jpeg(self):
        converted = heic_to_jpeg(_heic_upload('IMG_1234.heic'))

        self.assertEqual(converted.name, 'IMG_1234.jpg')
        self.assertEqual(converted.content_type, 'image/jpeg')
        image = Image.open(converted)
        self.assertEqual(image.format, 'JPEG')

    def test_oversized_heic_downscaled(self):
        upload = SimpleUploadedFile(
            'big.heic', _make_image_bytes('HEIF', size=(4000, 3000)),
            content_type='image/heic',
        )
        image = Image.open(heic_to_jpeg(upload))
        self.assertLessEqual(max(image.size), 2560)

    def test_jpeg_passes_through_unchanged(self):
        upload = SimpleUploadedFile(
            'photo.jpg', _make_image_bytes('JPEG'), content_type='image/jpeg'
        )
        self.assertIs(heic_to_jpeg(upload), upload)

    def test_none_and_false_pass_through(self):
        # None = no file submitted; False = "clear" checkbox ticked.
        self.assertIsNone(heic_to_jpeg(None))
        self.assertIs(heic_to_jpeg(False), False)

    def test_existing_fieldfile_passes_through_without_storage_access(self):
        # When the photo isn't changed, cleaned_data holds the model's
        # existing FieldFile. Its path may point at a file that no longer
        # exists (e.g. uploads from the serverless era), so it must be
        # returned untouched — not opened.
        horse = Horse(name='Stale', photo='horses/long_gone.jpg')
        field_file = horse.photo
        self.assertIs(heic_to_jpeg(field_file), field_file)


class HorseFormPhotoTests(TestCase):
    def setUp(self):
        self._media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._media_root, ignore_errors=True)

    def test_form_accepts_heic_photo(self):
        form = HorseForm(data={'name': 'Ali'}, files={'photo': _heic_upload()})
        form.full_clean()

        self.assertNotIn('photo', form.errors)
        self.assertTrue(form.cleaned_data['photo'].name.endswith('.jpg'))

    def test_heic_photo_saves_end_to_end(self):
        with override_settings(MEDIA_ROOT=self._media_root):
            form = HorseForm(data={'name': 'Ali'}, files={'photo': _heic_upload()})
            self.assertTrue(form.is_valid(), form.errors)
            horse = form.save()

        self.assertTrue(horse.photo.name.endswith('.jpg'))

    def test_save_with_stale_photo_path_does_not_crash(self):
        horse = Horse.objects.create(name='Stale', photo='horses/long_gone.jpg')
        form = HorseForm(data={'name': 'Stale renamed'}, instance=horse)

        self.assertTrue(form.is_valid(), form.errors)
        with override_settings(MEDIA_ROOT=self._media_root):
            saved = form.save()
        self.assertEqual(saved.name, 'Stale renamed')
