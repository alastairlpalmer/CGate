"""Regression tests for invoicing billing fixes.

Covers:
  #1 Xero CSV export must bill the owner's (possibly fractional) share, not
     days x full-daily-rate.
  #2 A placement on a horse with no OwnershipShare must still be billed to the
     placement owner.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from billing.models import ExtraCharge
from core.models import Horse, Location, Owner, OwnershipShare, Placement, RateType
from invoicing.services import InvoiceService
from invoicing.utils import invoice_to_xero_rows

PERIOD = (date(2026, 6, 1), date(2026, 6, 30))  # 30-day month


class SharelessPlacementBillingTests(TestCase):
    """#2 — placements without ownership shares must still be billed."""

    def setUp(self):
        self.owner = Owner.objects.create(name="Emma Evans", email="emma@example.com")
        self.loc = Location.objects.create(site="Colgate", name="Top Field")
        self.rate = RateType.objects.create(name="Grass", daily_rate=Decimal("5.00"))
        self.horse = Horse.objects.create(name="Ghost")
        # Placement, deliberately with NO OwnershipShare for the horse.
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.loc,
            rate_type=self.rate, start_date=date(2026, 5, 1),
        )

    def test_shareless_placement_is_billed_to_placement_owner(self):
        preview = InvoiceService.calculate_invoice_preview(self.owner, *PERIOD)
        # 30 days x £5 = £150, billed 100% to the placement owner.
        self.assertEqual(preview["total"], Decimal("150.00"))
        self.assertEqual(len(preview["livery_charges"]), 1)
        self.assertEqual(preview["livery_charges"][0]["share_percentage"], Decimal("100.00"))

    def test_shareless_owner_included_in_monthly_billing(self):
        owners = InvoiceService.get_owners_for_billing(*PERIOD)
        self.assertIn(self.owner, list(owners))

    def test_horse_with_shares_not_double_billed_by_fallback(self):
        # Give a *different* horse a full share and confirm the shareless
        # fallback doesn't also pick it up (no duplicate livery line).
        share_owner = Owner.objects.create(name="Alice")
        horse2 = Horse.objects.create(name="Thunder")
        OwnershipShare.objects.create(horse=horse2, owner=share_owner, share_percentage=Decimal("100.00"))
        Placement.objects.create(
            horse=horse2, owner=share_owner, location=self.loc,
            rate_type=self.rate, start_date=date(2026, 5, 1),
        )
        preview = InvoiceService.calculate_invoice_preview(share_owner, *PERIOD)
        self.assertEqual(len(preview["livery_charges"]), 1)
        self.assertEqual(preview["total"], Decimal("150.00"))


class XeroExportShareTests(TestCase):
    """#1 — Xero CSV rows must reflect the split line_total, not full charge."""

    def setUp(self):
        self.o1 = Owner.objects.create(name="Alice", email="a@example.com")
        self.o2 = Owner.objects.create(name="Bob", email="b@example.com")
        self.loc = Location.objects.create(site="Somerford", name="Paddock 1")
        self.rate = RateType.objects.create(name="Premium", daily_rate=Decimal("7.00"))
        self.horse = Horse.objects.create(name="Trio")
        OwnershipShare.objects.create(horse=self.horse, owner=self.o1, share_percentage=Decimal("60.00"))
        OwnershipShare.objects.create(horse=self.horse, owner=self.o2, share_percentage=Decimal("40.00"))
        Placement.objects.create(
            horse=self.horse, owner=self.o1, location=self.loc,
            rate_type=self.rate, start_date=date(2026, 5, 1),
        )

    def test_xero_livery_line_uses_split_amount(self):
        invoice = InvoiceService.create_invoice(self.o1, *PERIOD)
        # Full charge is 30 x £7 = £210; Alice's 60% share = £126.00.
        livery = invoice.line_items.get(line_type="livery")
        self.assertEqual(livery.line_total, Decimal("126.00"))

        rows = invoice_to_xero_rows(invoice)
        livery_row = next(r for r in rows if r["*Description"].startswith("Premium"))
        # Xero computes amount = Quantity x UnitAmount; it must equal £126.00,
        # NOT 30 x 7 = £210.
        qty = Decimal(livery_row["*Quantity"])
        unit = Decimal(livery_row["*UnitAmount"])
        self.assertEqual(qty * unit, Decimal("126.00"))

    def test_xero_line_amounts_sum_to_invoice_total(self):
        # Add an extra charge too, then confirm the CSV lines reconcile.
        ExtraCharge.objects.create(
            horse=self.horse, owner=self.o1, charge_type="vet",
            date=date(2026, 6, 10), description="Checkup",
            amount=Decimal("50.00"), split_by_ownership=False,
        )
        invoice = InvoiceService.create_invoice(self.o1, *PERIOD)
        rows = invoice_to_xero_rows(invoice)
        line_sum = sum(Decimal(r["*Quantity"]) * Decimal(r["*UnitAmount"]) for r in rows)
        self.assertEqual(line_sum, invoice.total)
