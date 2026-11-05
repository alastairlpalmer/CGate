"""An issued invoice keeps the yard's details from the day it was issued."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from core.models import BusinessSettings, Owner
from invoicing.models import Invoice


class IssuerDetailsTests(TestCase):

    def setUp(self):
        self.settings_row = BusinessSettings.get_settings()
        self.settings_row.business_name = 'Old Yard'
        self.settings_row.vat_registration = 'GB123456789'
        self.settings_row.save()
        self.invoice = Invoice.objects.create(
            owner=Owner.objects.create(name='Jo', email='jo@example.com'),
            period_start=date(2026, 6, 1), period_end=date(2026, 6, 30),
            due_date=date(2026, 7, 31), total=Decimal('100.00'),
        )

    def rename_yard(self):
        self.settings_row.business_name = 'New Yard'
        self.settings_row.vat_registration = ''
        self.settings_row.save()

    def test_a_draft_shows_the_current_details(self):
        self.rename_yard()
        self.assertEqual(self.invoice.issuer_details().business_name, 'New Yard')

    def test_sending_keeps_the_details_of_that_day(self):
        self.invoice.mark_as_sent()
        self.rename_yard()
        self.invoice.refresh_from_db()
        details = self.invoice.issuer_details()
        self.assertEqual(details.business_name, 'Old Yard')
        self.assertEqual(details.vat_registration, 'GB123456789')

    def test_any_save_as_issued_keeps_the_details(self):
        self.invoice.status = Invoice.Status.PAID
        self.invoice.save(update_fields=['status'])
        self.rename_yard()
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.issuer_details().business_name, 'Old Yard')

    def test_the_pdf_prints_the_issued_details(self):
        import reportlab.platypus as platypus

        from invoicing.pdf import generate_invoice_pdf_reportlab

        self.invoice.mark_as_sent()
        self.rename_yard()
        self.invoice.refresh_from_db()
        texts = []
        real = platypus.Paragraph

        def paragraph(text, *args, **kwargs):
            texts.append(str(text))
            return real(text, *args, **kwargs)

        with patch.object(platypus, 'Paragraph', paragraph):
            generate_invoice_pdf_reportlab(self.invoice)
        printed = '\n'.join(texts)
        self.assertIn('Old Yard', printed)
        self.assertIn('GB123456789', printed)
        self.assertNotIn('New Yard', printed)
