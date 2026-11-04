"""Usage dates and replaced profile photos: third bug hunt."""

import shutil
import tempfile
from datetime import timedelta
from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Horse, Location
from core.services import LocationUsageService


class FutureUsageTests(TestCase):

    def test_a_manual_usage_change_cannot_be_dated_in_the_future(self):
        field = Location.objects.create(name='A', site='S')
        with self.assertRaises(ValidationError):
            LocationUsageService.set_usage(
                field, usage=Location.Usage.HAY,
                change_date=timezone.localdate() + timedelta(days=30),
            )
        field.refresh_from_db()
        self.assertNotEqual(field.usage, Location.Usage.HAY)


class ReplacedPhotoTests(TestCase):

    def setUp(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, True)
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)

    def _jpeg(self, name):
        from PIL import Image
        buffer = BytesIO()
        Image.new('RGB', (40, 40), 'red').save(buffer, 'JPEG')
        return ContentFile(buffer.getvalue(), name=name)

    def test_a_replaced_profile_photo_file_is_deleted(self):
        horse = Horse.objects.create(name='Ghost')
        horse.photo = self._jpeg('one.jpg')
        horse.save()
        old_name = horse.photo.name
        storage = horse.photo.storage
        self.assertTrue(storage.exists(old_name))
        horse.photo = self._jpeg('two.jpg')
        horse.save()
        self.assertFalse(storage.exists(old_name))
        self.assertTrue(storage.exists(horse.photo.name))
