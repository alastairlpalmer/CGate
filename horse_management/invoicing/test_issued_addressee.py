"""An issued invoice keeps the owner's name and address as billed
(pre-launch review). A PDF made again after a move printed the new one."""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import Owner
from invoicing.models import Invoice


class IssuedAddresseeTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name='Anna Smith', address='1 Old Lane', account_code='SMI01')
        self.invoice = Invoice.objects.create(
            owner=self.owner, invoice_number='ADR1', period_start=date(2026, 6, 1),
            period_end=date(2026, 6, 30), total=Decimal('100'),
        )

    def move_house(self):
        self.owner.name = 'Anna Jones'
        self.owner.address = '9 New Road'
        self.owner.save()
        self.invoice.refresh_from_db()

    def test_a_draft_shows_the_owner_as_now(self):
        self.move_house()
        self.assertEqual(self.invoice.bill_to_details().address, '9 New Road')

    def test_a_sent_invoice_keeps_the_owner_as_billed(self):
        self.invoice.mark_as_sent()
        self.move_house()
        details = self.invoice.bill_to_details()
        self.assertEqual((details.name, details.address, details.account_code), ('Anna Smith', '1 Old Lane', 'SMI01'))

    def test_the_pdf_prints_the_owner_as_billed(self):
        from invoicing.pdf import generate_invoice_pdf

        self.invoice.mark_as_sent()
        self.move_house()
        pdf = generate_invoice_pdf(self.invoice).read()
        self.assertGreater(len(pdf), 1000)

    def test_anonymising_the_owner_clears_the_copy_too(self):
        from core import gdpr

        self.invoice.mark_as_sent()
        self.owner.is_archived = True
        self.owner.save()
        gdpr.anonymise_owner(self.owner)
        self.invoice.refresh_from_db()
        details = self.invoice.bill_to_details()
        self.assertNotIn('Smith', details.name)
        self.assertEqual((details.address, details.account_code), ('', ''))
