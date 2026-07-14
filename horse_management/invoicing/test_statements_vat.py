"""Tests for the finance-completion batch: VAT handling, aged debtors, and
owner statements."""

from datetime import date, timedelta
from decimal import Decimal

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import BusinessSettings, Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin, make_viewer
from invoicing.models import Invoice, Payment
from invoicing.services import InvoiceService, StatementService
from invoicing.utils import invoice_to_xero_rows
from xero_integration.services import build_xero_invoice_payload

PERIOD = (date(2026, 6, 1), date(2026, 6, 30))  # 30 days


def _placed_owner(name="Alice", email="a@example.com", horse_name="Ghost",
                  daily_rate="5.00"):
    owner = Owner.objects.create(name=name, email=email)
    loc = Location.objects.create(site="Colgate", name=f"Field-{horse_name}")
    rate = RateType.objects.create(name=f"Rate-{horse_name}", daily_rate=Decimal(daily_rate))
    horse = Horse.objects.create(name=horse_name)
    Placement.objects.create(
        horse=horse, owner=owner, location=loc,
        rate_type=rate, start_date=date(2026, 1, 1),
    )
    return owner


def _set_vat(rate):
    settings_obj = BusinessSettings.get_settings()
    settings_obj.vat_rate = Decimal(rate)
    settings_obj.save()


class VatTests(TestCase):

    def setUp(self):
        self.owner = _placed_owner()  # £150 net for June

    def test_vat_added_to_invoice_totals(self):
        _set_vat("20.00")
        invoice = InvoiceService.create_invoice(self.owner, *PERIOD)
        self.assertEqual(invoice.subtotal, Decimal("150.00"))
        self.assertEqual(invoice.vat_rate, Decimal("20.00"))
        self.assertEqual(invoice.vat_amount, Decimal("30.00"))
        self.assertEqual(invoice.total, Decimal("180.00"))

    def test_zero_rate_unchanged(self):
        invoice = InvoiceService.create_invoice(self.owner, *PERIOD)
        self.assertEqual(invoice.vat_amount, Decimal("0.00"))
        self.assertEqual(invoice.total, Decimal("150.00"))

    def test_preview_matches_invoice(self):
        _set_vat("20.00")
        preview = InvoiceService.calculate_invoice_preview(self.owner, *PERIOD)
        self.assertEqual(preview["vat_amount"], Decimal("30.00"))
        self.assertEqual(preview["total"], Decimal("180.00"))

    def test_rate_snapshotted_not_retroactive(self):
        _set_vat("20.00")
        invoice = InvoiceService.create_invoice(self.owner, *PERIOD)
        _set_vat("0.00")
        invoice.recalculate_totals()
        invoice.refresh_from_db()
        # Still 20% — the invoice keeps its snapshot.
        self.assertEqual(invoice.total, Decimal("180.00"))

    def test_csv_export_tax_type_follows_invoice_rate(self):
        _set_vat("20.00")
        invoice = InvoiceService.create_invoice(self.owner, *PERIOD)
        rows = invoice_to_xero_rows(invoice)
        self.assertEqual(rows[0]["*TaxType"], "20% (VAT on Income)")
        # Line amounts are NET; Xero adds the VAT and lands on invoice.total.
        line_sum = sum(
            Decimal(r["*Quantity"]) * Decimal(r["*UnitAmount"]) for r in rows
        )
        self.assertEqual(
            (line_sum * Decimal("1.20")).quantize(Decimal("0.01")), invoice.total
        )

    def test_csv_export_no_vat_when_rate_zero(self):
        invoice = InvoiceService.create_invoice(self.owner, *PERIOD)
        rows = invoice_to_xero_rows(invoice)
        self.assertEqual(rows[0]["*TaxType"], "No VAT")

    def test_xero_api_payload_consistent_with_pdf(self):
        _set_vat("20.00")
        invoice = InvoiceService.create_invoice(self.owner, *PERIOD)
        payload = build_xero_invoice_payload(invoice, "contact-id")
        self.assertEqual(payload["LineItems"][0]["TaxType"], "OUTPUT2")
        line_sum = sum(
            Decimal(l["Quantity"]) * Decimal(l["UnitAmount"])
            for l in payload["LineItems"]
        )
        self.assertEqual(
            (line_sum * Decimal("1.20")).quantize(Decimal("0.01")), invoice.total
        )

    def test_settings_form_rejects_odd_rates(self):
        from core.forms import BusinessSettingsForm

        form = BusinessSettingsForm(
            data={
                'business_name': 'Yard', 'vat_registration': 'GB123',
                'vat_rate': '17.50', 'default_payment_terms': 30,
                'invoice_prefix': 'INV',
            },
            instance=BusinessSettings.get_settings(),
        )
        self.assertFalse(form.is_valid())
        self.assertIn('vat_rate', form.errors)


class AgedDebtorsTests(TestCase):

    def setUp(self):
        self.staff = make_admin()
        today = timezone.now().date()
        self.o1 = _placed_owner("Alice", "a@example.com", "Ghost")
        self.o2 = _placed_owner("Bob", "b@example.com", "Thunder")
        # Alice: one invoice 45 days overdue with a £50 part payment
        self.inv1 = InvoiceService.create_invoice(self.o1, *PERIOD)
        self.inv1.due_date = today - timedelta(days=45)
        self.inv1.save(update_fields=['due_date'])
        self.inv1.mark_as_sent()
        Payment.objects.create(invoice=self.inv1, date=today, amount=Decimal("50.00"))
        # Bob: one invoice not yet due
        self.inv2 = InvoiceService.create_invoice(self.o2, *PERIOD)
        self.inv2.due_date = today + timedelta(days=10)
        self.inv2.save(update_fields=['due_date'])
        self.inv2.mark_as_sent()

    def test_buckets_and_balances(self):
        rows, totals = StatementService.aged_debtors()
        by_name = {r['owner'].name: r for r in rows}
        alice = by_name['Alice']
        self.assertEqual(alice['b31_60'], Decimal("100.00"))  # 150 - 50 paid
        self.assertEqual(alice['total'], Decimal("100.00"))
        bob = by_name['Bob']
        self.assertEqual(bob['current'], Decimal("150.00"))
        self.assertEqual(totals['total'], Decimal("250.00"))

    def test_paid_and_draft_excluded(self):
        self.inv2.mark_as_paid()
        rows, totals = StatementService.aged_debtors()
        names = [r['owner'].name for r in rows]
        self.assertNotIn('Bob', names)

    def test_view_renders(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("aged_debtors"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alice")


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class OwnerStatementTests(TestCase):

    def setUp(self):
        self.staff = make_admin()
        self.owner = _placed_owner()
        self.inv = InvoiceService.create_invoice(self.owner, *PERIOD)
        self.inv.mark_as_sent()
        Payment.objects.create(
            invoice=self.inv, date=date(2026, 7, 1), amount=Decimal("60.00")
        )

    def test_statement_builder(self):
        statement = StatementService.build_owner_statement(self.owner)
        self.assertEqual(len(statement['rows']), 1)
        row = statement['rows'][0]
        self.assertEqual(row['paid'], Decimal("60.00"))
        self.assertEqual(row['balance'], Decimal("90.00"))
        self.assertEqual(statement['totals']['balance'], Decimal("90.00"))

    def test_cancelled_invoices_excluded(self):
        cancelled = InvoiceService.create_invoice(
            self.owner, date(2026, 5, 1), date(2026, 5, 31)
        )
        cancelled.status = Invoice.Status.CANCELLED
        cancelled.save(update_fields=['status'])
        statement = StatementService.build_owner_statement(self.owner)
        self.assertEqual(len(statement['rows']), 1)

    def test_statement_page_renders(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("owner_statement", args=[self.owner.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "90.00")

    def test_statement_pdf_downloads(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            reverse("owner_statement_pdf", args=[self.owner.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_statement_email_sends_with_attachment(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("owner_statement_email", args=[self.owner.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertIn(self.owner.email, message.to)
        self.assertEqual(len(message.attachments), 1)
        self.assertTrue(message.attachments[0][0].endswith(".pdf"))

    def test_statement_email_requires_owner_email(self):
        self.owner.email = ""
        self.owner.save(update_fields=['email'])
        self.client.force_login(self.staff)
        self.client.post(reverse("owner_statement_email", args=[self.owner.pk]))
        self.assertEqual(len(mail.outbox), 0)

    def test_viewer_cannot_email(self):
        viewer = make_viewer()  # invoices=view — no write access
        self.client.force_login(viewer)
        response = self.client.post(
            reverse("owner_statement_email", args=[self.owner.pk])
        )
        self.assertEqual(response.status_code, 403)
