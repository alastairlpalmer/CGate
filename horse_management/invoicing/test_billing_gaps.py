"""Regression tests for billing gaps found in the July 2026 QA review.

M1: split charges on departed co-owned horses must reach the monthly run.
M2: the health-record ↔ charge sync must not resurrect deleted charges,
    leave £0.00 charges behind, or silently skip owner-less horses.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import ExtraCharge
from core.models import (
    BusinessSettings, Horse, Location, Owner, OwnershipShare, Placement,
    RateType,
)
from core.roles_testutils import make_admin
from health.models import Vaccination, VaccinationType
from health.views import sync_record_charge
from invoicing.services import InvoiceService


class DepartedCoOwnedSplitChargeTests(TestCase):
    """M1 — a vet bill entered after a co-owned horse departed must still be
    billed by the next monthly run."""

    def setUp(self):
        BusinessSettings.get_settings()
        self.alice = Owner.objects.create(name="Alice")
        self.bob = Owner.objects.create(name="Bob")
        self.loc = Location.objects.create(site="Main", name="Field")
        self.rate = RateType.objects.create(name="Grass", daily_rate=Decimal("10"))
        self.horse = Horse.objects.create(name="Duo", is_active=False)
        OwnershipShare.objects.create(
            horse=self.horse, owner=self.alice,
            share_percentage=Decimal("50"), is_primary_contact=True,
        )
        OwnershipShare.objects.create(
            horse=self.horse, owner=self.bob, share_percentage=Decimal("50"),
        )
        # Departed in May — no placement overlaps June.
        Placement.objects.create(
            horse=self.horse, owner=self.alice, location=self.loc,
            rate_type=self.rate,
            start_date=date(2026, 3, 1), end_date=date(2026, 5, 25),
        )
        # Vet bill dated May, entered after the May invoices went out.
        ExtraCharge.objects.create(
            horse=self.horse, owner=self.alice, charge_type='vet',
            date=date(2026, 5, 20), description='Emergency callout',
            amount=Decimal('240.00'), split_by_ownership=True,
        )

    def test_owners_discovered_for_billing(self):
        owners = set(
            InvoiceService.get_owners_for_billing(
                date(2026, 6, 1), date(2026, 6, 30)
            )
        )
        self.assertIn(self.alice, owners)
        self.assertIn(self.bob, owners)

    def test_monthly_run_bills_both_owners(self):
        invoices, _ = InvoiceService.generate_monthly_invoices(2026, 6)
        totals = {i.owner.name: i.subtotal for i in invoices}
        self.assertEqual(totals.get("Alice"), Decimal("120.00"))
        self.assertEqual(totals.get("Bob"), Decimal("120.00"))
        charge = ExtraCharge.objects.get()
        self.assertTrue(charge.invoiced)


class HealthChargeSyncTests(TestCase):
    """M2 — the record→charge sync cluster."""

    def setUp(self):
        self.owner = Owner.objects.create(name="Sue")
        self.horse = Horse.objects.create(name="Dobbin")
        OwnershipShare.objects.create(
            horse=self.horse, owner=self.owner,
            share_percentage=Decimal("100"), is_primary_contact=True,
        )
        self.vt = VaccinationType.objects.create(
            name="Flu", interval_months=12, reminder_days_before=30,
        )

    def _record(self, cost=Decimal('45.00')):
        record = Vaccination.objects.create(
            horse=self.horse, vaccination_type=self.vt,
            date_given=timezone.localdate(), cost=cost,
        )
        sync_record_charge(record)
        record.refresh_from_db()
        return record

    def test_zeroing_cost_deletes_uninvoiced_charge(self):
        record = self._record()
        self.assertIsNotNone(record.extra_charge)
        record.cost = Decimal('0.00')
        record.save()
        status = sync_record_charge(record)
        record.refresh_from_db()
        self.assertEqual(status, 'deleted')
        self.assertIsNone(record.extra_charge)
        self.assertEqual(ExtraCharge.objects.count(), 0)

    def test_no_owner_returns_status(self):
        self.horse.ownership_shares.all().delete()
        record = Vaccination.objects.create(
            horse=self.horse, vaccination_type=self.vt,
            date_given=timezone.localdate(), cost=Decimal('18.50'),
        )
        self.assertEqual(sync_record_charge(record), 'no_owner')
        self.assertEqual(ExtraCharge.objects.count(), 0)

    def test_invoiced_cost_edit_reports_divergence(self):
        record = self._record()
        charge = record.extra_charge
        charge.invoiced = True
        charge.save(update_fields=['invoiced'])
        record.cost = Decimal('65.00')
        record.save()
        self.assertEqual(sync_record_charge(record), 'invoiced')
        charge.refresh_from_db()
        self.assertEqual(charge.amount, Decimal('45.00'))  # bill unchanged

    def test_charge_delete_view_zeroes_record_cost(self):
        record = self._record()
        charge = record.extra_charge
        self.client.force_login(make_admin(username='charge-admin'))
        response = self.client.post(reverse('charge_delete', args=[charge.pk]))
        self.assertEqual(response.status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.cost, Decimal('0.00'))
        self.assertIsNone(record.extra_charge)
        # The kill shot: a later edit of the record must NOT resurrect it.
        record.notes = 'typo fixed'
        record.save()
        self.assertIsNone(sync_record_charge(record))
        self.assertEqual(ExtraCharge.objects.count(), 0)
