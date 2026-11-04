"""Invoice output and webhooks: findings from the third bug hunt."""

from datetime import datetime, timedelta
from datetime import timezone as dt_tz
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from billing.models import ExtraCharge
from core.models import BusinessSettings, Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin
from invoicing.models import Invoice
from invoicing.pdf import generate_invoice_pdf
from invoicing.services import InvoiceService
from invoicing.tasks import claim_invoice_for_sending, send_invoice_email_task
from invoicing.utils import invoice_to_xero_rows


def pdf_text(buffer):
    from pypdf import PdfReader
    return '\n'.join(page.extract_text() for page in PdfReader(buffer).pages)


class Base(TestCase):
    def make(self):
        self.owner = Owner.objects.create(name='Ann', email='a@example.com')
        location = Location.objects.create(site='S', name='F')
        rate = RateType.objects.create(name='R', daily_rate=Decimal('5.00'))
        horse = Horse.objects.create(name='H')
        end = timezone.localdate().replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
        Placement.objects.create(
            horse=horse, owner=self.owner, location=location, rate_type=rate,
            start_date=start - timedelta(days=10),
        )
        ExtraCharge.objects.create(
            horse=horse, owner=self.owner, charge_type='vet', description='Vet visit',
            date=start, amount=Decimal('40.00'), split_by_ownership=False,
        )
        return InvoiceService.create_invoice(self.owner, start, end)


class SendInFlightTests(Base):

    def test_a_draft_being_emailed_cannot_be_deleted(self):
        invoice = self.make()
        claim_invoice_for_sending(invoice.pk)
        self.client.force_login(make_admin())
        self.client.post(f'/invoicing/{invoice.pk}/delete/')
        self.assertTrue(Invoice.objects.filter(pk=invoice.pk).exists())

    def test_a_draft_being_emailed_cannot_be_cancelled(self):
        invoice = self.make()
        claim_invoice_for_sending(invoice.pk)
        self.client.force_login(make_admin())
        self.client.post(f'/invoicing/{invoice.pk}/edit/', {
            'status': 'cancelled', 'customer_reference': '', 'payment_terms_days': 30,
            'due_date': invoice.due_date.isoformat(), 'notes': '',
        })
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.DRAFT)

    def test_an_invoice_cancelled_during_the_send_stays_cancelled(self):
        invoice = self.make()
        claim_invoice_for_sending(invoice.pk)

        def cancel_meanwhile(inv):
            Invoice.objects.filter(pk=inv.pk).update(status=Invoice.Status.CANCELLED)
            return True

        with patch('notifications.emails.send_invoice_email', side_effect=cancel_meanwhile):
            self.assertEqual(send_invoice_email_task(invoice.pk), 'skipped')
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.CANCELLED)

    def test_an_invoice_deleted_during_the_send_is_not_a_crash(self):
        invoice = self.make()
        claim_invoice_for_sending(invoice.pk)

        def delete_meanwhile(inv):
            Invoice.objects.filter(pk=inv.pk).delete()
            return True

        with patch('notifications.emails.send_invoice_email', side_effect=delete_meanwhile):
            self.assertEqual(send_invoice_email_task(invoice.pk), 'skipped')


class OutputTests(Base):

    def test_every_xero_csv_line_names_its_invoice(self):
        rows = invoice_to_xero_rows(self.make())
        self.assertEqual(len(rows), 2)
        for row in rows:
            for column in ('*ContactName', '*InvoiceNumber', '*InvoiceDate', '*DueDate'):
                self.assertTrue(row[column], column)

    def test_the_pdf_date_is_the_uk_date(self):
        invoice = self.make()
        # 00:30 BST on 1 August is 23:30 UTC on 31 July.
        Invoice.objects.filter(pk=invoice.pk).update(
            created_at=datetime(2026, 7, 31, 23, 30, tzinfo=dt_tz.utc),
        )
        invoice.refresh_from_db()
        self.assertIn('Date: 01/08/2026', pdf_text(generate_invoice_pdf(invoice)))

    def test_the_vat_label_has_no_trailing_zeros(self):
        settings_row = BusinessSettings.get_settings()
        settings_row.vat_rate = Decimal('20.00')
        settings_row.save()
        invoice = self.make()
        invoice.refresh_from_db()
        self.assertIn('VAT (20%)', pdf_text(generate_invoice_pdf(invoice)))


@override_settings(EMAIL_WEBHOOK_TOKEN='s3cret')
class WebhookTests(TestCase):

    def test_a_non_ascii_token_is_refused_not_a_500(self):
        self.client.raise_request_exception = False
        response = self.client.post(
            '/email/events/postmark/%C3%A9t%C3%A9/', data='{}', content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)

    def test_a_body_that_is_not_an_object_is_a_400(self):
        self.client.raise_request_exception = False
        response = self.client.post(
            '/email/events/postmark/s3cret/', data='[1]', content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
