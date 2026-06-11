"""Tests for HEIC/HEIF photo upload handling.

iPhones upload photos as HEIC by default. These uploads must validate
(pillow-heif opener registered in CoreConfig.ready) and be converted to
JPEG (core.images.heic_to_jpeg) so browsers can display them.
"""

import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

from core.forms import HorseForm
from core.images import heic_to_jpeg


def _make_image_bytes(fmt):
    img = Image.new('RGB', (32, 32), 'red')
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

    def test_jpeg_passes_through_unchanged(self):
        upload = SimpleUploadedFile(
            'photo.jpg', _make_image_bytes('JPEG'), content_type='image/jpeg'
        )
        self.assertIs(heic_to_jpeg(upload), upload)

    def test_none_and_false_pass_through(self):
        # None = no file submitted; False = "clear" checkbox ticked.
        self.assertIsNone(heic_to_jpeg(None))
        self.assertIs(heic_to_jpeg(False), False)

    def test_horse_form_accepts_heic_photo(self):
        form = HorseForm(data={}, files={'photo': _heic_upload()})
        form.full_clean()

        self.assertNotIn('photo', form.errors)
        self.assertTrue(form.cleaned_data['photo'].name.endswith('.jpg'))
