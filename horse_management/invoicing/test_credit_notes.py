"""Credit notes, stored as numbered payments of method CREDIT_NOTE."""

from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import BusinessSettings, Owner
from core.roles_testutils import make_admin, make_user_with_access
from invoicing.forms import PaymentForm
from invoicing.models import Invoice, Payment
from invoicing.reconciliation import monthly_report
from invoicing.services import CreditNoteRefused, issue_credit_note


class CreditNoteTests(TestCase):
    def setUp(self):
        BusinessSettings.get_settings()
        self.owner = Owner.objects.create(name='Credit Owner')
        self.invoice = Invoice.objects.create(
            owner=self.owner, invoice_number='CRD-1',
            period_start=date(2026, 3, 1), period_end=date(2026, 3, 31),
            subtotal=Decimal('300'), total=Decimal('300'), status=Invoice.Status.SENT,
        )
        self.admin = make_admin('cnadmin')
        self.client.force_login(self.admin)

    def _issue(self, amount='50.00', reason='Horse away 5 days'):
        return self.client.post(reverse('credit_note_create', args=[self.invoice.pk]), {
            'date': timezone.localdate().isoformat(), 'amount': amount, 'reason': reason,
        })

    def test_issue_lowers_the_balance_and_is_numbered(self):
        response = self._issue()
        self.assertRedirects(response, reverse('invoice_detail', args=[self.invoice.pk]))
        credit = Payment.objects.get(method=Payment.Method.CREDIT_NOTE)
        self.assertEqual(credit.credit_note_number, 'CN00001')
        self.assertEqual(credit.notes, 'Horse away 5 days')
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.balance_due, Decimal('250.00'))
        self.assertEqual(self.invoice.amount_credited, Decimal('50.00'))
        self.assertEqual(self.invoice.amount_received, Decimal('0.00'))

    def test_numbers_run_in_sequence(self):
        self._issue('10')
        self._issue('20')
        numbers = list(Payment.objects.filter(method=Payment.Method.CREDIT_NOTE)
                       .order_by('pk').values_list('credit_note_number', flat=True))
        self.assertEqual(numbers, ['CN00001', 'CN00002'])

    def test_full_credit_marks_the_invoice_paid(self):
        self._issue('300.00')
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.Status.PAID)

    def test_cannot_credit_more_than_owed(self):
        response = self._issue('301.00')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Payment.objects.filter(method=Payment.Method.CREDIT_NOTE).exists())

    def test_drafts_cannot_be_credited(self):
        self.invoice.status = Invoice.Status.DRAFT
        self.invoice.save()
        with self.assertRaises(CreditNoteRefused):
            issue_credit_note(self.invoice, Decimal('10'), 'x', date.today())

    def test_reason_is_required(self):
        self._issue(reason='')
        self.assertFalse(Payment.objects.filter(method=Payment.Method.CREDIT_NOTE).exists())

    def test_pdf_and_invoice_page(self):
        self._issue()
        credit = Payment.objects.get(method=Payment.Method.CREDIT_NOTE)
        pdf = self.client.get(reverse('credit_note_pdf', args=[credit.pk]))
        self.assertEqual(pdf['Content-Type'], 'application/pdf')
        self.assertIn('CN00001.pdf', pdf['Content-Disposition'])
        page = self.client.get(reverse('invoice_detail', args=[self.invoice.pk]))
        self.assertContains(page, 'CN00001')
        self.assertContains(page, 'Issue Credit Note')

    def test_void_keeps_the_number_used(self):
        self._issue()
        credit = Payment.objects.get(method=Payment.Method.CREDIT_NOTE)
        credit.void(self.admin, 'Issued by mistake')
        self._issue('5')
        self.assertTrue(Payment.objects.filter(credit_note_number='CN00002').exists())

    def test_payment_form_does_not_offer_credit_note(self):
        form = PaymentForm(invoice=self.invoice)
        self.assertNotIn(Payment.Method.CREDIT_NOTE, [c[0] for c in form.fields['method'].choices])

    def test_view_only_user_cannot_issue(self):
        self.client.force_login(make_user_with_access('cnview', invoices='view'))
        self._issue()
        self.assertFalse(Payment.objects.filter(method=Payment.Method.CREDIT_NOTE).exists())

    def test_reconciliation_separates_credits_from_money(self):
        Payment.objects.create(invoice=self.invoice, date=timezone.localdate(), amount=Decimal('100'))
        issue_credit_note(self.invoice, Decimal('40'), 'Away', timezone.localdate())
        month = monthly_report(timezone.localdate().year).months[-1]
        self.assertEqual(month.received, Decimal('100.00'))
        self.assertEqual(month.credited, Decimal('40.00'))
