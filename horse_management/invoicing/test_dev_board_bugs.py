"""Dev board "Bugs" cards, checked again on 21 Nov 2026.

- "Horse name on Xero invoice": Xero's flat list of lines did not say which
  horse each line was for.
- "Send button on Invoices does not work": on a live server with no mail
  server set up, Send "sent" the invoice into the logs and marked it sent.
"""

import csv
import io
from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin
from invoicing.models import Invoice
from invoicing.services import InvoiceService
from invoicing.tasks import claim_invoice_for_sending, send_invoice_email_task
from invoicing.utils import write_xero_csv
from notifications.mail import EMAIL_NOT_SET_UP, email_is_set_up
from xero_integration.services import build_xero_invoice_payload

CONSOLE = 'django.core.mail.backends.console.EmailBackend'


def _invoice(horses=('Avicii', 'Ballerina'), email='jo@example.com'):
    owner = Owner.objects.create(name='Jo Bloggs', email=email)
    loc = Location.objects.create(site='S', name='Top Field')
    rate = RateType.objects.create(name='Grass livery', daily_rate=Decimal('5.00'))
    end = timezone.localdate().replace(day=1) - timedelta(days=1)
    for name in horses:
        Placement.objects.create(horse=Horse.objects.create(name=name), owner=owner, location=loc,
                                 rate_type=rate, start_date=end.replace(day=1) - timedelta(days=10))
    return InvoiceService.create_invoice(owner, end.replace(day=1), end)


class XeroHorseNameTests(TestCase):

    def test_each_line_says_its_horse(self):
        invoice = _invoice()
        lines = list(invoice.line_items.select_related('horse'))
        self.assertEqual(len(lines), 2)
        for line in lines:
            self.assertTrue(line.description_with_horse.startswith(f'{line.horse.name}: '))
            self.assertTrue(line.description_with_horse.endswith(line.description))

    def test_api_payload_and_csv_carry_the_horse(self):
        invoice = _invoice()
        payload = build_xero_invoice_payload(invoice, 'contact-id')
        descriptions = sorted(item['Description'] for item in payload['LineItems'])
        self.assertTrue(descriptions[0].startswith('Avicii: '))
        self.assertTrue(descriptions[1].startswith('Ballerina: '))
        out = io.StringIO()
        write_xero_csv([invoice], out)
        rows = list(csv.DictReader(io.StringIO(out.getvalue())))
        self.assertEqual(sorted(r['*Description'].split(':')[0] for r in rows), ['Avicii', 'Ballerina'])

    def test_no_horse_or_name_already_there(self):
        invoice = _invoice(horses=('Avicii',))
        line = invoice.line_items.get()
        line.description = 'Avicii - transport'
        self.assertEqual(line.description_with_horse, 'Avicii - transport')
        line.horse = None
        line.description = 'Yard fee'
        self.assertEqual(line.description_with_horse, 'Yard fee')


class EmailSetUpTests(TestCase):

    def setUp(self):
        self.client.force_login(make_admin())

    def test_the_check(self):
        with override_settings(DEBUG=False, EMAIL_BACKEND=CONSOLE):
            self.assertFalse(email_is_set_up())
        with override_settings(DEBUG=True, EMAIL_BACKEND=CONSOLE):
            self.assertTrue(email_is_set_up())  # a developer's machine
        with override_settings(DEBUG=False, EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend'):
            self.assertTrue(email_is_set_up())

    def test_send_says_email_is_not_set_up_and_keeps_the_draft(self):
        invoice = _invoice()
        with override_settings(DEBUG=False, EMAIL_BACKEND=CONSOLE):
            response = self.client.post(reverse('invoice_send', args=[invoice.pk]), follow=True)
        self.assertContains(response, 'Email is not set up on this server')
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.DRAFT)
        self.assertIsNone(invoice.sent_at)

    def test_the_queued_send_stops_too(self):
        invoice = _invoice()
        self.assertTrue(claim_invoice_for_sending(invoice.pk))
        with override_settings(DEBUG=False, EMAIL_BACKEND=CONSOLE):
            self.assertEqual(send_invoice_email_task(invoice.pk), 'failed')
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.DRAFT)
        self.assertEqual(invoice.send_error, EMAIL_NOT_SET_UP)
        self.assertFalse(invoice.is_send_queued)

    def test_send_still_works_with_a_mail_server(self):
        invoice = _invoice()
        response = self.client.post(reverse('invoice_send', args=[invoice.pk]), follow=True)
        self.assertContains(response, 'Invoice sent to jo@example.com')
        self.assertEqual(len(mail.outbox), 1)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.SENT)
