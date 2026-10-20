"""PO references, the VAT line, the reconciliation report and invoice
number gaps. docs/LAUNCH_READINESS_PLAN.md, P1 work package 5.
Stepped overdue reminders are covered in notifications/test_reminders.py."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import BusinessSettings, Owner
from core.roles_testutils import make_admin, make_user_with_access
from invoicing import pdf as pdf_module
from invoicing.models import Invoice, Payment
from invoicing.reconciliation import monthly_report, number_gaps
from invoicing.utils import invoice_to_xero_rows
from xero_integration.services import build_xero_invoice_payload


def _invoice(owner, number, total='100.00', status=Invoice.Status.SENT, created=None, **extra):
    inv = Invoice.objects.create(
        owner=owner, invoice_number=number,
        period_start=date(2026, 3, 1), period_end=date(2026, 3, 31),
        subtotal=Decimal(total), total=Decimal(total), status=status, **extra,
    )
    if created:
        Invoice.objects.filter(pk=inv.pk).update(created_at=created)
        inv.refresh_from_db()
    return inv


def _pdf_texts(invoice):
    """Every Paragraph text the invoice PDF was built from."""
    import reportlab.platypus
    real = reportlab.platypus.Paragraph
    with mock.patch.object(reportlab.platypus, 'Paragraph', side_effect=real) as spy:
        pdf_module.generate_invoice_pdf(invoice)
    return ' '.join(str(c.args[0]) for c in spy.call_args_list if c.args)


class CustomerReferenceTests(TestCase):
    def setUp(self):
        BusinessSettings.get_settings()
        self.owner = Owner.objects.create(name='Acme Riding Club', account_code='ACME', invoice_reference='PO-4471')

    def test_owner_reference_is_copied_onto_new_invoices(self):
        from invoicing.services import InvoiceService
        from billing.models import ExtraCharge
        from core.models import Horse
        horse = Horse.objects.create(name='Ref Horse')
        ExtraCharge.objects.create(horse=horse, owner=self.owner, charge_type='vet', date=date(2026, 3, 10),
                                   description='Vet', amount=Decimal('50'), split_by_ownership=False)
        invoice = InvoiceService.create_invoice(self.owner, date(2026, 3, 1), date(2026, 3, 31))
        self.assertEqual(invoice.customer_reference, 'PO-4471')

    def test_reference_is_on_the_pdf_and_sent_to_xero(self):
        from invoicing.models import InvoiceLineItem
        invoice = _invoice(self.owner, 'REF-1', customer_reference='PO-4471')
        InvoiceLineItem.objects.create(
            invoice=invoice, line_type=InvoiceLineItem.LineType.LIVERY, description='Livery',
            quantity=Decimal('1'), unit_price=Decimal('100'),
        )
        self.assertIn('Your ref: PO-4471', _pdf_texts(invoice))
        self.assertEqual(build_xero_invoice_payload(invoice, 'contact-1')['Reference'], 'PO-4471')
        self.assertEqual(invoice_to_xero_rows(invoice)[0]['Reference'], 'PO-4471')

    def test_account_code_is_the_fallback_reference(self):
        invoice = _invoice(self.owner, 'REF-2')
        self.assertEqual(build_xero_invoice_payload(invoice, 'contact-1')['Reference'], 'ACME')

    def test_reference_can_be_edited_on_the_invoice(self):
        invoice = _invoice(self.owner, 'REF-3', status=Invoice.Status.DRAFT)
        self.client.force_login(make_admin('refadmin'))
        self.client.post(reverse('invoice_update', args=[invoice.pk]), {
            'status': 'draft', 'customer_reference': 'PO-9999', 'payment_terms_days': 30,
            'due_date': '2026-04-30', 'notes': '',
        })
        invoice.refresh_from_db()
        self.assertEqual(invoice.customer_reference, 'PO-9999')


class VatLineTests(TestCase):
    def setUp(self):
        self.settings_row = BusinessSettings.get_settings()
        self.invoice = _invoice(Owner.objects.create(name='Vat Owner'), 'VAT-1')

    def test_not_registered_prints_no_vat_line(self):
        self.settings_row.vat_registration = 'N/A'
        self.settings_row.save()
        self.assertNotIn('VAT Reg', _pdf_texts(self.invoice))

    def test_registered_prints_the_number(self):
        self.settings_row.vat_registration = 'GB123456789'
        self.settings_row.save()
        self.assertIn('VAT Reg: GB123456789', _pdf_texts(self.invoice))


class ReconciliationTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name='Rec Owner')
        tz = timezone.get_current_timezone()
        jan = datetime(2026, 1, 15, 12, tzinfo=tz)
        feb = datetime(2026, 2, 10, 12, tzinfo=tz)
        self.a = _invoice(self.owner, 'INV00001', '100.00', created=jan, vat_amount=Decimal('0'))
        self.b = _invoice(self.owner, 'INV00002', '50.00', created=feb)
        _invoice(self.owner, 'INV00003', '999.00', status=Invoice.Status.DRAFT, created=feb)
        _invoice(self.owner, 'INV00004', '999.00', status=Invoice.Status.CANCELLED, created=feb)
        Payment.objects.create(invoice=self.a, date=date(2026, 2, 3), amount=Decimal('60'))
        voided = Payment.objects.create(invoice=self.a, date=date(2026, 2, 4), amount=Decimal('40'))
        voided.void(None, 'Bounced')

    def test_month_by_month(self):
        report = monthly_report(2026)
        jan, feb = report.months[0], report.months[1]
        self.assertEqual((jan.invoice_count, jan.total, jan.received, jan.outstanding),
                         (1, Decimal('100.00'), Decimal('0.00'), Decimal('100.00')))
        # Draft and cancelled left out; voided payment left out.
        self.assertEqual((feb.invoice_count, feb.total, feb.received, feb.outstanding),
                         (1, Decimal('50.00'), Decimal('60.00'), Decimal('90.00')))
        self.assertEqual(report.totals.total, Decimal('150.00'))
        self.assertEqual(report.cancelled_count, 1)

    def test_page_and_csv(self):
        self.client.force_login(make_user_with_access('finview', finances='full'))
        page = self.client.get(reverse('reconciliation'), {'year': 2026})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Jan 2026')
        csv = self.client.get(reverse('reconciliation'), {'year': 2026, 'format': 'csv'})
        self.assertIn('Total invoiced', csv.content.decode('utf-8-sig'))
        self.assertIn('150.00', csv.content.decode('utf-8-sig'))

    def test_needs_finance_access(self):
        self.client.force_login(make_user_with_access('nofin', dashboard='full'))
        self.assertNotEqual(self.client.get(reverse('reconciliation')).status_code, 200)


class NumberGapTests(TestCase):
    def test_deleted_draft_explains_its_gap(self):
        owner = Owner.objects.create(name='Gap Owner')
        admin = make_admin('gapadmin', email='gapadmin@example.com')
        _invoice(owner, 'INV00010')
        draft = _invoice(owner, 'INV00011', status=Invoice.Status.DRAFT)
        _invoice(owner, 'INV00013')
        self.client.force_login(admin)
        self.client.post(reverse('invoice_delete', args=[draft.pk]))
        self.assertFalse(Invoice.objects.filter(invoice_number='INV00011').exists())

        gaps = {g.number: g.explanation for g in number_gaps('INV')}
        self.assertEqual(set(gaps), {'INV00011', 'INV00012'})
        self.assertIn('gapadmin@example.com', gaps['INV00011'])
        self.assertIn('draft', gaps['INV00011'])
        self.assertIn('No record', gaps['INV00012'])

    def test_no_gaps(self):
        owner = Owner.objects.create(name='Tidy Owner')
        _invoice(owner, 'INV00001')
        _invoice(owner, 'INV00002')
        self.assertEqual(number_gaps('INV'), [])


class StatementTimeTests(TestCase):
    def test_reminder_detail_shows_on_invoice(self):
        owner = Owner.objects.create(name='Shown Owner', pause_reminders=True)
        invoice = _invoice(owner, 'SHOW-1', overdue_reminder_count=2,
                           last_overdue_reminder_at=timezone.now() - timedelta(days=3))
        self.client.force_login(make_admin('showadmin'))
        page = self.client.get(reverse('invoice_detail', args=[invoice.pk]))
        self.assertContains(page, 'Reminders Sent')
        self.assertContains(page, 'paused for this owner')
