"""Polish items from the pre-launch review."""

import io

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from core.roles_testutils import make_user_with_access


def _jpeg_with_gps():
    from PIL import Image

    image = Image.new('RGB', (40, 30), 'green')
    exif = Image.Exif()
    gps = exif.get_ifd(0x8825)
    gps[1] = 'N'
    gps[2] = (51.0, 30.0, 0.0)
    buffer = io.BytesIO()
    image.save(buffer, format='JPEG', exif=exif.tobytes())
    return buffer.getvalue()


class PhotoGpsTests(TestCase):
    def test_gps_is_removed_from_a_photo(self):
        from PIL import Image

        from core.images import _normalise_upload

        data = _jpeg_with_gps()
        self.assertTrue(Image.open(io.BytesIO(data)).getexif().get(0x8825))
        upload = SimpleUploadedFile('ghost.jpg', data, content_type='image/jpeg')
        result = _normalise_upload(upload, downscale_oversized=False)
        result.seek(0)
        self.assertFalse(Image.open(result).getexif().get(0x8825))


class EmailLinkHostTests(TestCase):
    @override_settings(SITE_URL='https://app.example.com')
    def test_a_password_reset_link_uses_site_url(self):
        user = make_user_with_access('reset@example.com', dashboard='full')
        user.email = 'reset@example.com'
        user.save()
        self.client.post(reverse('password_reset'), {'email': 'reset@example.com'}, HTTP_HOST='testserver')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('https://app.example.com/', mail.outbox[0].body)
        self.assertNotIn('http://testserver/', mail.outbox[0].body)
