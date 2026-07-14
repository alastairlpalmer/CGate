"""Tests for the payment ledger (partial payments, balances, status sync)."""

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin, make_viewer
from invoicing.models import Invoice, Payment
from invoicing.services import InvoiceService

PERIOD = (date(2026, 6, 1), date(2026, 6, 30))


def _invoice(name="Alice", email="a@example.com", horse_name="Ghost"):
    owner = Owner.objects.create(name=name, email=email)
    loc = Location.objects.create(site="Colgate", name=f"Field-{horse_name}")
    rate = RateType.objects.create(name=f"Rate-{horse_name}", daily_rate=Decimal("5.00"))
    horse = Horse.objects.create(name=horse_name)
    Placement.objects.create(
        horse=horse, owner=owner, location=loc,
        rate_type=rate, start_date=date(2026, 5, 1),
    )
    return InvoiceService.create_invoice(owner, *PERIOD)  # £150 draft


class PaymentLedgerModelTests(TestCase):

    def setUp(self):
        self.invoice = _invoice()  # £150

    def _pay(self, amount, **kwargs):
        payment = Payment.objects.create(
            invoice=self.invoice, date=date(2026, 7, 1),
            amount=Decimal(amount), **kwargs,
        )
        self.invoice.refresh_payment_status()
        self.invoice.refresh_from_db()
        return payment

    def test_partial_payment_keeps_invoice_open(self):
        self.invoice.mark_as_sent()
        self._pay("50.00")
        self.assertEqual(self.invoice.amount_paid, Decimal("50.00"))
        self.assertEqual(self.invoice.balance_due, Decimal("100.00"))
        self.assertEqual(self.invoice.status, Invoice.Status.SENT)

    def test_full_payment_marks_paid(self):
        self.invoice.mark_as_sent()
        self._pay("50.00")
        self._pay("100.00")
        self.assertEqual(self.invoice.status, Invoice.Status.PAID)
        self.assertIsNotNone(self.invoice.paid_at)
        self.assertEqual(self.invoice.balance_due, Decimal("0.00"))

    def test_partial_payment_promotes_draft_to_sent(self):
        self.assertEqual(self.invoice.status, Invoice.Status.DRAFT)
        self._pay("50.00")
        self.assertEqual(self.invoice.status, Invoice.Status.SENT)

    def test_full_payment_on_draft_marks_paid(self):
        self._pay("150.00")
        self.assertEqual(self.invoice.status, Invoice.Status.PAID)

    def test_mark_as_paid_records_balancing_payment(self):
        self.invoice.mark_as_sent()
        Payment.objects.create(
            invoice=self.invoice, date=date(2026, 7, 1), amount=Decimal("40.00")
        )
        self.invoice.mark_as_paid(reference="Marked as paid")
        self.invoice.refresh_from_db()
        # The ledger must agree with the PAID status.
        self.assertEqual(self.invoice.amount_paid, self.invoice.total)
        balancing = self.invoice.payments.order_by('-created_at').first()
        self.assertEqual(balancing.amount, Decimal("110.00"))

    def test_deleting_payment_reverts_paid_status(self):
        self.invoice.mark_as_sent()
        payment = self._pay("150.00")
        self.assertEqual(self.invoice.status, Invoice.Status.PAID)

        payment.delete()
        self.invoice.refresh_payment_status()
        self.invoice.refresh_from_db()
        # Due date is period_end + 30d = 30 Jul 2026, in the future relative
        # to nothing here — status depends on today, so accept either open state.
        self.assertIn(
            self.invoice.status, (Invoice.Status.SENT, Invoice.Status.OVERDUE)
        )
        self.assertIsNone(self.invoice.paid_at)

    def test_deleted_payment_past_due_reverts_to_overdue(self):
        self.invoice.due_date = timezone.now().date() - timedelta(days=5)
        self.invoice.save(update_fields=['due_date'])
        self.invoice.mark_as_sent()
        payment = self._pay("150.00")
        payment.delete()
        self.invoice.refresh_payment_status()
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.Status.OVERDUE)


class PaymentViewTests(TestCase):

    def setUp(self):
        self.staff = make_admin()
        self.viewer = make_viewer()  # invoices=view — no write access
        self.invoice = _invoice()
        self.invoice.mark_as_sent()
        self.url = reverse("payment_create", args=[self.invoice.pk])

    def test_record_payment(self):
        self.client.force_login(self.staff)
        response = self.client.post(self.url, {
            "date": "2026-07-01", "amount": "60.00",
            "method": "cash", "reference": "", "notes": "",
        })
        self.assertRedirects(response, reverse("invoice_detail", args=[self.invoice.pk]))
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.balance_due, Decimal("90.00"))
        self.assertEqual(self.invoice.status, Invoice.Status.SENT)

    def test_overpayment_rejected(self):
        self.client.force_login(self.staff)
        response = self.client.post(self.url, {
            "date": "2026-07-01", "amount": "200.00",
            "method": "cash", "reference": "", "notes": "",
        })
        self.assertEqual(response.status_code, 200)  # re-renders with error
        self.assertEqual(self.invoice.payments.count(), 0)
        self.assertIn("exceeds the outstanding balance", response.content.decode())

    def test_cancelled_invoice_blocked(self):
        self.invoice.status = Invoice.Status.CANCELLED
        self.invoice.save(update_fields=["status"])
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("invoice_detail", args=[self.invoice.pk]))

    def test_form_prefills_balance(self):
        Payment.objects.create(
            invoice=self.invoice, date=date(2026, 7, 1), amount=Decimal("50.00")
        )
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertEqual(
            response.context["form"].initial["amount"], Decimal("100.00")
        )

    def test_viewer_forbidden(self):
        self.client.force_login(self.viewer)
        # Insufficient access on a plain GET redirects to the dashboard...
        self.assertRedirects(self.client.get(self.url), "/")
        # ...and an attempted write is rejected outright.
        response = self.client.post(self.url, {
            "date": "2026-07-01", "amount": "60.00",
            "method": "cash", "reference": "", "notes": "",
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.invoice.payments.count(), 0)

    def test_delete_payment(self):
        payment = Payment.objects.create(
            invoice=self.invoice, date=date(2026, 7, 1), amount=Decimal("150.00")
        )
        self.invoice.refresh_payment_status()
        self.client.force_login(self.staff)
        response = self.client.post(reverse("payment_delete", args=[payment.pk]))
        self.assertRedirects(response, reverse("invoice_detail", args=[self.invoice.pk]))
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.payments.count(), 0)
        self.assertNotEqual(self.invoice.status, Invoice.Status.PAID)

    def test_one_click_mark_paid_still_works_and_records_payment(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse("invoice_mark_paid", args=[self.invoice.pk]))
        self.assertEqual(response.status_code, 302)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.Status.PAID)
        self.assertEqual(self.invoice.amount_paid, self.invoice.total)


class OutstandingKpiTests(TestCase):

    def test_finances_outstanding_subtracts_part_payments(self):
        staff = make_admin()
        invoice = _invoice()
        invoice.mark_as_sent()
        Payment.objects.create(
            invoice=invoice, date=date(2026, 7, 1), amount=Decimal("50.00")
        )
        self.client.force_login(staff)
        response = self.client.get(reverse("finances"))
        self.assertEqual(response.context["outstanding_total"], Decimal("100.00"))

    def test_dashboard_outstanding_shows_balance(self):
        staff = make_admin()
        invoice = _invoice()
        invoice.mark_as_sent()
        Payment.objects.create(
            invoice=invoice, date=date(2026, 7, 1), amount=Decimal("50.00")
        )
        self.client.force_login(staff)
        response = self.client.get(reverse("dashboard"))
        rows = response.context["outstanding_invoices"]
        self.assertEqual(rows[0].balance, Decimal("100.00"))
