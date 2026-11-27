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


class CreditNoteVatTests(TestCase):
    """Pre-launch review: credit notes had no VAT split."""

    def _rows(self, vat_rate, subtotal, total, amount):
        from unittest import mock

        import reportlab.platypus as platypus

        from invoicing.models import Payment
        from invoicing.pdf import generate_credit_note_pdf

        owner = Owner.objects.create(name='Vat Owner')
        invoice = Invoice.objects.create(
            owner=owner, invoice_number='VAT1', period_start=date(2026, 6, 1), period_end=date(2026, 6, 30),
            status=Invoice.Status.SENT, subtotal=subtotal, vat_rate=vat_rate, total=total,
        )
        credit = Payment.objects.create(invoice=invoice, amount=amount, date=date(2026, 7, 1),
                                        method=Payment.Method.CREDIT_NOTE, notes='Overcharge')
        seen = []
        real = platypus.Table

        def spy(data, *args, **kwargs):
            seen.append(data)
            return real(data, *args, **kwargs)

        with mock.patch.object(platypus, 'Table', spy):
            generate_credit_note_pdf(credit)
        return [row for table in seen for row in table if isinstance(row[0], str)]

    def test_a_credit_on_a_vat_invoice_shows_net_and_vat(self):
        rows = self._rows(Decimal('20'), Decimal('100.00'), Decimal('120.00'), Decimal('60.00'))
        self.assertIn(['Net', '£50.00'], rows)
        self.assertIn(['VAT (20%)', '£10.00'], rows)
        self.assertIn(['Total credited', '£60.00'], rows)

    def test_no_vat_lines_without_vat(self):
        rows = self._rows(Decimal('0'), Decimal('100.00'), Decimal('100.00'), Decimal('60.00'))
        self.assertNotIn('Net', [row[0] for row in rows])
