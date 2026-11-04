"""Locations, imports and horse photos: third bug hunt (each test failed before the fix)."""
import io, shutil, tempfile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from core.models import Horse
from core.roles_testutils import make_admin


class Bomb(TestCase):
    def test_bomb_png(self):
        from PIL import Image
        media = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, media, True)
        with override_settings(MEDIA_ROOT=media):
            b = io.BytesIO(); Image.new('1', (14000, 14000)).save(b, 'PNG')
            print('png bytes', len(b.getvalue()))
            h = Horse.objects.create(name='H')
            self.client.force_login(make_admin())
            self.client.raise_request_exception = False
            r = self.client.post(reverse('horse_photo_add', args=[h.pk]), {
                'category': 'condition', 'images': SimpleUploadedFile('pano.png', b.getvalue(), 'image/png')})
            self.assertNotEqual(r.status_code, 500)
