"""Regression tests: charges on a live invoice, "Paid" via the edit form,
and the invoice page surviving a line whose charge was deleted."""

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import ExtraCharge, ServiceProvider
from core.models import Horse, Location, Owner, OwnershipShare, Placement, RateType
from core.roles_testutils import make_admin
from health.models import FarrierVisit
from health.views import sync_record_charge
from invoicing.models import Invoice, InvoiceLineItem
from invoicing.services import InvoiceService
from invoicing.utils import group_line_items_by_horse


def _period():
    end = timezone.localdate().replace(day=1) - timedelta(days=1)
    return end.replace(day=1), end


class SplitChargeOnLiveInvoiceTests(TestCase):
    """A split charge on a co-owned horse stays invoiced=False until every
    co-owner is billed, but one owner's live invoice already carries a line
    for it. It must not be editable or deletable in that state."""

    def setUp(self):
        self.client.force_login(make_admin())
        self.a = Owner.objects.create(name="Alice", email="a@example.com")
        self.b = Owner.objects.create(name="Bob", email="b@example.com")
        loc = Location.objects.create(site="S", name="F")
        rate = RateType.objects.create(name="R", daily_rate=Decimal("5.00"))
        self.horse = Horse.objects.create(name="Ghost")
        start, end = _period()
        Placement.objects.create(
            horse=self.horse, owner=self.a, location=loc, rate_type=rate,
            start_date=start - timedelta(days=10),
        )
        OwnershipShare.objects.create(horse=self.horse, owner=self.a, share_percentage=Decimal("50"), is_primary_contact=True)
        OwnershipShare.objects.create(horse=self.horse, owner=self.b, share_percentage=Decimal("50"))
        self.charge = ExtraCharge.objects.create(
            horse=self.horse, owner=self.a, charge_type="vet", description="Vet",
            date=start + timedelta(days=3), amount=Decimal("120.00"),
            split_by_ownership=True,
        )
        self.invoice = InvoiceService.create_invoice(self.a, start, end)
        self.charge.refresh_from_db()
        self.assertFalse(self.charge.invoiced)  # Bob not yet billed
        self.assertTrue(self.invoice.line_items.filter(charge=self.charge).exists())

    def test_delete_is_refused(self):
        resp = self.client.post(reverse('charge_delete', args=[self.charge.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(ExtraCharge.objects.filter(pk=self.charge.pk).exists())

    def test_edit_is_refused(self):
        resp = self.client.get(reverse('charge_update', args=[self.charge.pk]))
        self.assertRedirects(resp, reverse('charge_list'), fetch_redirect_response=False)

    def test_cancelled_invoice_releases_the_guard(self):
        self.invoice.status = Invoice.Status.CANCELLED
        self.invoice.save(update_fields=['status'])
        resp = self.client.get(reverse('charge_update', args=[self.charge.pk]))
        self.assertEqual(resp.status_code, 200)

    def test_anonymous_user_is_sent_to_login_not_told_about_the_charge(self):
        self.client.logout()
        resp = self.client.get(reverse('charge_delete', args=[self.charge.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/accounts/login/', resp['Location'])


class HealthRecordSyncOnLiveInvoiceTests(TestCase):
    """The charge screens are not the only way into a split charge.

    sync_record_charge rewrites the charge behind a health record whenever
    the record is saved, and it used to ask ``charge.invoiced`` — which a
    split charge keeps False until the last co-owner is billed. Editing the
    record's cost after the first co-owner's invoice therefore billed the
    two owners fractions of two different totals, and clearing the cost
    deleted the charge out from under a line already issued.
    """

    def setUp(self):
        self.a = Owner.objects.create(name="Alice", email="a@example.com")
        self.b = Owner.objects.create(name="Bob", email="b@example.com")
        loc = Location.objects.create(site="S", name="F")
        rate = RateType.objects.create(name="R", daily_rate=Decimal("5.00"))
        self.horse = Horse.objects.create(name="Ghost")
        self.start, self.end = _period()
        Placement.objects.create(
            horse=self.horse, owner=self.a, location=loc, rate_type=rate,
            start_date=self.start - timedelta(days=10),
        )
        OwnershipShare.objects.create(
            horse=self.horse, owner=self.a,
            share_percentage=Decimal("50"), is_primary_contact=True,
        )
        OwnershipShare.objects.create(
            horse=self.horse, owner=self.b, share_percentage=Decimal("50"),
        )
        provider = ServiceProvider.objects.create(
            name="Smith", provider_type="farrier",
        )
        self.visit = FarrierVisit.objects.create(
            horse=self.horse, service_provider=provider, date=self.start,
            work_done="trim", cost=Decimal("100.00"),
        )
        self.assertEqual(sync_record_charge(self.visit), 'created')
        self.charge = self.visit.extra_charge
        self.assertTrue(self.charge.split_by_ownership)

        # Alice is invoiced; Bob is not, so the charge still reads unbilled.
        self.invoice_a = InvoiceService.create_invoice(self.a, self.start, self.end)
        self.charge.refresh_from_db()
        self.assertFalse(self.charge.invoiced)
        self.line_a = InvoiceLineItem.objects.get(
            invoice=self.invoice_a, charge=self.charge,
        )
        self.assertEqual(self.line_a.line_total, Decimal("50.00"))

    def test_cost_edit_is_refused_so_the_split_still_sums(self):
        self.visit.cost = Decimal("200.00")
        self.visit.save()

        self.assertEqual(sync_record_charge(self.visit), 'invoiced')
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.amount, Decimal("100.00"))

        invoice_b = InvoiceService.create_invoice(self.b, self.start, self.end)
        line_b = InvoiceLineItem.objects.get(invoice=invoice_b, charge=self.charge)
        self.assertEqual(
            self.line_a.line_total + line_b.line_total, self.charge.amount,
        )

    def test_clearing_the_cost_does_not_orphan_the_issued_line(self):
        self.visit.cost = Decimal("0.00")
        self.visit.save()

        self.assertEqual(sync_record_charge(self.visit), 'invoiced')
        self.assertTrue(ExtraCharge.objects.filter(pk=self.charge.pk).exists())
        self.line_a.refresh_from_db()
        self.assertEqual(self.line_a.charge_id, self.charge.pk)

    def test_cancelling_the_only_invoice_releases_the_guard(self):
        self.invoice_a.status = Invoice.Status.CANCELLED
        self.invoice_a.save(update_fields=['status'])
        self.invoice_a.release_extra_charges()

        self.visit.cost = Decimal("200.00")
        self.visit.save()
        self.assertEqual(sync_record_charge(self.visit), 'updated')
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.amount, Decimal("200.00"))

    def test_a_singly_owned_horse_still_syncs_freely(self):
        owner = Owner.objects.create(name="Carol", email="c@example.com")
        loc = Location.objects.create(site="S", name="F2")
        rate = RateType.objects.create(name="R2", daily_rate=Decimal("5.00"))
        horse = Horse.objects.create(name="Blue")
        Placement.objects.create(
            horse=horse, owner=owner, location=loc, rate_type=rate,
            start_date=self.start - timedelta(days=10),
        )
        OwnershipShare.objects.create(
            horse=horse, owner=owner,
            share_percentage=Decimal("100"), is_primary_contact=True,
        )
        visit = FarrierVisit.objects.create(
            horse=horse, service_provider=None, date=self.start,
            work_done="trim", cost=Decimal("40.00"),
        )
        self.assertEqual(sync_record_charge(visit), 'created')
        visit.cost = Decimal("60.00")
        visit.save()
        self.assertEqual(sync_record_charge(visit), 'updated')
        visit.extra_charge.refresh_from_db()
        self.assertEqual(visit.extra_charge.amount, Decimal("60.00"))
        visit.cost = Decimal("0.00")
        visit.save()
        self.assertEqual(sync_record_charge(visit), 'deleted')


class OrphanedLineSortTests(TestCase):
    def test_grouping_survives_a_line_with_no_charge(self):
        owner = Owner.objects.create(name="Alice")
        horse = Horse.objects.create(name="Ghost")
        start, end = _period()
        invoice = Invoice.objects.create(
            owner=owner, period_start=start, period_end=end,
            due_date=end + timedelta(days=14),
        )
        charge = ExtraCharge.objects.create(
            horse=horse, owner=owner, charge_type="vet", description="Vet",
            date=start, amount=Decimal("10.00"), split_by_ownership=False,
        )
        InvoiceLineItem.objects.create(
            invoice=invoice, horse=horse, line_type='extra', description='a',
            quantity=1, unit_price=Decimal("10.00"), line_total=Decimal("10.00"),
            charge=charge,
        )
        InvoiceLineItem.objects.create(
            invoice=invoice, horse=horse, line_type='extra', description='b',
            quantity=1, unit_price=Decimal("5.00"), line_total=Decimal("5.00"),
            charge=None,
        )
        groups = group_line_items_by_horse(invoice.line_items.all())  # must not raise
        self.assertEqual(len(groups[0]['items']), 2)


class PaidViaEditFormTests(TestCase):
    def setUp(self):
        self.client.force_login(make_admin())
        owner = Owner.objects.create(name="Alice", email="a@example.com")
        loc = Location.objects.create(site="S", name="F")
        rate = RateType.objects.create(name="R", daily_rate=Decimal("5.00"))
        horse = Horse.objects.create(name="Ghost")
        start, end = _period()
        Placement.objects.create(
            horse=horse, owner=owner, location=loc, rate_type=rate,
            start_date=start - timedelta(days=10),
        )
        self.invoice = InvoiceService.create_invoice(owner, start, end)
        self.invoice.mark_as_sent()

    def test_status_paid_records_balancing_payment(self):
        resp = self.client.post(
            reverse('invoice_update', args=[self.invoice.pk]),
            {
                'status': 'paid',
                'payment_terms_days': self.invoice.payment_terms_days,
                'due_date': self.invoice.due_date.isoformat(),
                'notes': '',
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.Status.PAID)
        self.assertEqual(self.invoice.balance_due, Decimal("0.00"))
        self.assertIsNotNone(self.invoice.paid_at)
        self.assertEqual(self.invoice.payments.count(), 1)

    def test_double_mark_paid_records_one_payment(self):
        url = reverse('invoice_mark_paid', args=[self.invoice.pk])
        self.client.post(url)
        self.client.post(url)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.payments.count(), 1)
        self.assertEqual(self.invoice.balance_due, Decimal("0.00"))
