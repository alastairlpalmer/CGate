"""Regression tests for invoicing billing fixes.

Covers:
  #1 Xero CSV export must bill the owner's (possibly fractional) share, not
     days x full-daily-rate.
  #2 A placement on a horse with no OwnershipShare must still be billed to the
     placement owner.
  #3 Moving a horse to a new owner bills the old owner for pre-move days and the
     new owner for post-move days (and transfers the ownership share).
  #4 Ownership shares that do not total 100% must not silently under-bill.
"""

from datetime import date
from decimal import Decimal

from django.forms import inlineformset_factory
from django.test import TestCase

from billing.models import ExtraCharge
from core.forms import BaseOwnershipShareFormSet, OwnershipShareForm
from core.models import Horse, Location, Owner, OwnershipShare, Placement, RateType
from core.services import PlacementService
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


class MoveToNewOwnerBillingTests(TestCase):
    """#3 — an ownership change via move splits billing across the two owners."""

    def setUp(self):
        self.old = Owner.objects.create(name="Old Owner", email="old@example.com")
        self.new = Owner.objects.create(name="New Owner", email="new@example.com")
        self.loc_a = Location.objects.create(site="Colgate", name="Top Field")
        self.loc_b = Location.objects.create(site="Colgate", name="Bottom Field")
        self.rate = RateType.objects.create(name="Grass", daily_rate=Decimal("5.00"))
        self.horse, _ = PlacementService.create_new_arrival(
            name="Mover", owner=self.old, location=self.loc_a,
            rate_type=self.rate, arrival_date=date(2026, 6, 1),
        )

    def test_move_splits_billing_between_owners(self):
        PlacementService.move_horse(
            self.horse, new_location=self.loc_b, move_date=date(2026, 6, 16),
            new_owner=self.new,
        )
        # 1-15 Jun (15 days) to old owner, 16-30 Jun (15 days) to new owner.
        self.assertEqual(
            InvoiceService.calculate_invoice_preview(self.old, *PERIOD)["total"],
            Decimal("75.00"),
        )
        self.assertEqual(
            InvoiceService.calculate_invoice_preview(self.new, *PERIOD)["total"],
            Decimal("75.00"),
        )

    def test_move_transfers_ownership_share(self):
        PlacementService.move_horse(
            self.horse, new_location=self.loc_b, move_date=date(2026, 6, 16),
            new_owner=self.new,
        )
        horse = Horse.objects.get(pk=self.horse.pk)
        owners = [s.owner_id for s in horse.ownership_shares.all()]
        self.assertEqual(owners, [self.new.id])
        self.assertEqual(horse.current_owner, self.new)


class SubHundredPercentShareTests(TestCase):
    """#4 — shares that don't total 100% must not silently under-bill."""

    def setUp(self):
        self.loc = Location.objects.create(site="Somerford", name="Paddock 1")
        self.rate = RateType.objects.create(name="Grass", daily_rate=Decimal("5.00"))

    def _place(self, horse, owner):
        Placement.objects.create(
            horse=horse, owner=owner, location=self.loc,
            rate_type=self.rate, start_date=date(2026, 5, 1),
        )

    def test_single_sub_100_share_billed_full_to_placement_owner(self):
        owner = Owner.objects.create(name="Half")
        horse = Horse.objects.create(name="HalfSingle")
        OwnershipShare.objects.create(horse=horse, owner=owner, share_percentage=Decimal("50.00"))
        self._place(horse, owner)
        # 30 x £5 = £150 billed in full, not £75.
        self.assertEqual(
            InvoiceService.calculate_invoice_preview(owner, *PERIOD)["total"],
            Decimal("150.00"),
        )

    def test_co_owned_remainder_billed_to_primary(self):
        a = Owner.objects.create(name="CoA")
        b = Owner.objects.create(name="CoB")
        horse = Horse.objects.create(name="CoHorse")
        OwnershipShare.objects.create(horse=horse, owner=a, share_percentage=Decimal("60.00"), is_primary_contact=True)
        OwnershipShare.objects.create(horse=horse, owner=b, share_percentage=Decimal("30.00"))
        self._place(horse, a)
        # Full £150: primary A gets 60% + 10% remainder = £105, B gets 30% = £45.
        ta = InvoiceService.calculate_invoice_preview(a, *PERIOD)["total"]
        tb = InvoiceService.calculate_invoice_preview(b, *PERIOD)["total"]
        self.assertEqual(ta, Decimal("105.00"))
        self.assertEqual(tb, Decimal("45.00"))
        self.assertEqual(ta + tb, Decimal("150.00"))


class OwnershipFormsetValidationTests(TestCase):
    """#4 — the ownership formset must require shares to total exactly 100%."""

    def setUp(self):
        self.horse = Horse.objects.create(name="FormsetHorse")
        self.a = Owner.objects.create(name="A")
        self.b = Owner.objects.create(name="B")
        self.FS = inlineformset_factory(
            Horse, OwnershipShare, form=OwnershipShareForm,
            formset=BaseOwnershipShareFormSet, extra=0,
        )

    def _run(self, pcts):
        data = {
            "ownership_shares-TOTAL_FORMS": str(len(pcts)),
            "ownership_shares-INITIAL_FORMS": "0",
            "ownership_shares-MIN_NUM_FORMS": "0",
            "ownership_shares-MAX_NUM_FORMS": "1000",
        }
        for i, (owner, pct) in enumerate(pcts):
            data[f"ownership_shares-{i}-owner"] = str(owner.pk)
            data[f"ownership_shares-{i}-share_percentage"] = str(pct)
        return self.FS(data, instance=self.horse)

    def test_partial_total_rejected(self):
        self.assertFalse(self._run([(self.a, "60"), (self.b, "30")]).is_valid())

    def test_exact_100_accepted(self):
        self.assertTrue(self._run([(self.a, "60"), (self.b, "40")]).is_valid())

    def test_no_shares_allowed(self):
        self.assertTrue(self._run([]).is_valid())
