"""Statements show only issued invoices; an invoice with money against it
cannot be cancelled."""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import Owner
from invoicing.forms import InvoiceUpdateForm
from invoicing.models import Invoice, Payment
from invoicing.services import StatementService


def make_invoice(owner, number, total, status):
    return Invoice.objects.create(
        owner=owner, invoice_number=number,
        period_start=date(2026, 6, 1), period_end=date(2026, 6, 30),
        subtotal=Decimal(total), total=Decimal(total), status=status,
        due_date=date(2026, 7, 30),
    )


class StatementTests(TestCase):
    def test_drafts_are_not_on_the_statement(self):
        owner = Owner.objects.create(name='Petra')
        make_invoice(owner, 'INV-D', '300.00', Invoice.Status.DRAFT)
        make_invoice(owner, 'INV-S', '120.00', Invoice.Status.SENT)
        statement = StatementService.build_owner_statement(owner)
        self.assertEqual([row['invoice'].invoice_number for row in statement['rows']], ['INV-S'])
        self.assertEqual(statement['totals']['balance'], Decimal('120.00'))


class CancelWithPaymentsTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name='Petra')
        self.invoice = make_invoice(self.owner, 'INV-1', '300.00', Invoice.Status.SENT)

    def _cancel(self):
        return InvoiceUpdateForm({
            'status': Invoice.Status.CANCELLED,
            'customer_reference': '', 'payment_terms_days': 30,
            'due_date': '2026-07-30', 'notes': '',
        }, instance=self.invoice)

    def test_an_invoice_with_a_payment_cannot_be_cancelled(self):
        Payment.objects.create(invoice=self.invoice, date=date(2026, 7, 5), amount=Decimal('100.00'))
        form = self._cancel()
        self.assertFalse(form.is_valid())
        self.assertIn('Void them first', str(form.errors['status']))

    def test_an_invoice_with_no_payment_can_still_be_cancelled(self):
        self.assertTrue(self._cancel().is_valid())
