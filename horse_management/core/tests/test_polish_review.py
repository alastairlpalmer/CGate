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


class ErrorTextTests(TestCase):
    """Pre-launch review: some messages showed Python list brackets."""

    def test_a_refused_departure_has_no_brackets(self):
        from datetime import date

        from django.contrib.messages import get_messages

        from core.models import Horse, Location, Owner, Placement, RateType
        from core.roles_testutils import make_admin

        self.client.force_login(make_admin('departadmin'))
        horse = Horse.objects.create(name='Ghost')
        Placement.objects.create(
            horse=horse, owner=Owner.objects.create(name='Anna'),
            location=Location.objects.create(site='Colgate', name='Top'),
            rate_type=RateType.objects.create(name='Grass', daily_rate=10), start_date=date(2026, 6, 1),
        )
        response = self.client.post(reverse('horse_depart', args=[horse.pk]), {'departure_date': '2026-05-01'})
        texts = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(texts)
        self.assertFalse(any(t.startswith("['") for t in texts), texts)


class InvoiceEmailBalanceTests(TestCase):
    def test_a_part_paid_invoice_email_says_what_is_still_owed(self):
        from datetime import date
        from decimal import Decimal

        from django.template.loader import render_to_string

        from core.models import BusinessSettings, Owner
        from invoicing.models import Invoice, Payment

        invoice = Invoice.objects.create(
            owner=Owner.objects.create(name='Anna'), invoice_number='EM1', period_start=date(2026, 6, 1),
            period_end=date(2026, 6, 30), status=Invoice.Status.SENT, total=Decimal('100.00'),
        )
        Payment.objects.create(invoice=invoice, amount=Decimal('40.00'), date=date(2026, 7, 1))
        html = render_to_string('notifications/email/invoice.html',
                                {'invoice': invoice, 'business': BusinessSettings.get_settings()})
        self.assertIn('£60.00', html)
        self.assertIn('£40.00', html)
