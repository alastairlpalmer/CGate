"""Tests for horse photo avatar thumbnails."""

import io
import shutil
import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import Horse

TEMP_MEDIA = tempfile.mkdtemp(prefix='cgate-thumb-tests-')


def _photo(name="dobbin.jpg", size=(1600, 1200), color=(120, 90, 60)):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new('RGB', size, color).save(buffer, format='JPEG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/jpeg')


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class ThumbnailTests(TestCase):

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def test_thumbnail_generated_on_save(self):
        horse = Horse.objects.create(name="Dobbin", photo=_photo())
        horse.refresh_from_db()
        self.assertTrue(horse.photo_thumb)
        from PIL import Image

        with horse.photo_thumb.open('rb') as fh:
            image = Image.open(fh)
            self.assertEqual(image.size, (320, 320))
        # Thumb is dramatically smaller than the original.
        self.assertLess(horse.photo_thumb.size, horse.photo.size)

    def test_no_photo_no_thumb(self):
        horse = Horse.objects.create(name="Plain")
        self.assertFalse(horse.photo_thumb)

    def test_photo_change_regenerates_thumb(self):
        horse = Horse.objects.create(name="Dobbin", photo=_photo())
        first_thumb = horse.photo_thumb.name
        horse.photo = _photo("new.jpg", color=(20, 200, 20))
        horse.save()
        horse.refresh_from_db()
        self.assertTrue(horse.photo_thumb)
        self.assertNotEqual(horse.photo_thumb.name, first_thumb)

    def test_clearing_photo_clears_thumb(self):
        horse = Horse.objects.create(name="Dobbin", photo=_photo())
        horse.photo = None
        horse.save()
        horse.refresh_from_db()
        self.assertFalse(horse.photo_thumb)

    def test_unrelated_save_does_not_touch_thumb(self):
        horse = Horse.objects.create(name="Dobbin", photo=_photo())
        thumb_name = horse.photo_thumb.name
        horse.notes = "changed"
        horse.save()
        horse.refresh_from_db()
        self.assertEqual(horse.photo_thumb.name, thumb_name)

    def test_update_fields_save_still_works(self):
        horse = Horse.objects.create(name="Dobbin", photo=_photo())
        horse.is_active = False
        horse.save(update_fields=['is_active'])
        horse.refresh_from_db()
        self.assertFalse(horse.is_active)
        self.assertTrue(horse.photo_thumb)

    def test_backfill_command(self):
        horse = Horse.objects.create(name="Dobbin", photo=_photo())
        # Simulate a pre-thumbnail record.
        Horse.objects.filter(pk=horse.pk).update(photo_thumb='')
        out = io.StringIO()
        call_command('generate_thumbnails', stdout=out, stderr=io.StringIO())
        horse.refresh_from_db()
        self.assertTrue(horse.photo_thumb)
        self.assertIn("Thumbnails generated: 1", out.getvalue())

    def test_avatar_partial_prefers_thumb(self):
        user = User.objects.create_user("viewer", password="pw")
        self.client.force_login(user)
        horse = Horse.objects.create(name="Dobbin", photo=_photo())
        response = self.client.get(reverse("horse_detail", args=[horse.pk]))
        horse.refresh_from_db()
        self.assertContains(response, horse.photo_thumb.url)
