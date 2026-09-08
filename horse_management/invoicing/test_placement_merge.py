"""Regression tests: field movements must not split a livery charge.

A move ends one placement and opens another. Billed one line per placement,
a horse moved twice in a period showed three near-identical lines on the
invoice (INV00038: "2 days", "28 days", "5 days" at the same £6.00 rate).
Consecutive placements at the same price must bill as ONE line; a new line
starts only when the price changes.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import Horse, Location, Owner, OwnershipShare, Placement, RateType
from core.services import PlacementService
from invoicing.services import InvoiceService

# The period from the reported invoice.
PERIOD = (date(2026, 7, 1), date(2026, 9, 8))


class FieldMovementMergeTests(TestCase):
    """Same price across a move => one line."""

    def setUp(self):
        self.owner = Owner.objects.create(
            name="Rebecca Osbourne", email="rebecca@example.com"
        )
        self.field_a = Location.objects.create(site="Colgate", name="Top Field")
        self.field_b = Location.objects.create(site="Colgate", name="Mid Field")
        self.field_c = Location.objects.create(site="Colgate", name="Low Field")
        self.rate = RateType.objects.create(
            name="Horse grazing incl hay", daily_rate=Decimal("6.00")
        )
        self.horse, self.placement = PlacementService.create_new_arrival(
            name="Mal De Mer",
            owner=self.owner,
            location=self.field_a,
            rate_type=self.rate,
            arrival_date=date(2026, 8, 5),
        )

    def _move(self, to_location, on, rate_type=None):
        # Re-read: current_placement is cached on the instance.
        self.horse = Horse.objects.get(pk=self.horse.pk)
        return PlacementService.move_horse(
            self.horse,
            new_location=to_location,
            move_date=on,
            new_rate_type=rate_type,
        )

    def test_two_moves_at_one_price_bill_as_a_single_line(self):
        self._move(self.field_b, date(2026, 8, 7))
        self._move(self.field_c, date(2026, 9, 4))

        preview = InvoiceService.calculate_invoice_preview(self.owner, *PERIOD)
        charges = preview["livery_charges"]

        self.assertEqual(len(charges), 1, [c["description"] for c in charges])
        # 5 Aug to 8 Sep inclusive = 27 + 8 = 35 days at £6.00.
        self.assertEqual(charges[0]["days"], 35)
        self.assertEqual(charges[0]["amount"], Decimal("210.00"))
        self.assertEqual(preview["total"], Decimal("210.00"))
        self.assertIn("35 days (5 Aug to 8 Sep 2026)", charges[0]["description"])

    def test_invoice_carries_one_livery_line_item(self):
        self._move(self.field_b, date(2026, 8, 7))
        self._move(self.field_c, date(2026, 9, 4))

        invoice = InvoiceService.create_invoice(self.owner, *PERIOD)
        livery = list(invoice.line_items.filter(line_type="livery"))

        self.assertEqual(len(livery), 1, [i.description for i in livery])
        self.assertEqual(livery[0].quantity, Decimal("35.00"))
        self.assertEqual(livery[0].unit_price, Decimal("6.00"))
        self.assertEqual(livery[0].line_total, Decimal("210.00"))
        self.assertEqual(invoice.total, Decimal("210.00"))

    def test_a_price_change_still_starts_a_new_line(self):
        dearer = RateType.objects.create(
            name="Horse in stable", daily_rate=Decimal("24.00")
        )
        self._move(self.field_b, date(2026, 9, 4), rate_type=dearer)

        preview = InvoiceService.calculate_invoice_preview(self.owner, *PERIOD)
        charges = sorted(preview["livery_charges"], key=lambda c: c["days"])

        self.assertEqual(len(charges), 2)
        self.assertEqual(charges[0]["days"], 5)  # 4-8 Sep at £24
        self.assertEqual(charges[0]["amount"], Decimal("120.00"))
        self.assertEqual(charges[1]["days"], 30)  # 5 Aug-3 Sep at £6
        self.assertEqual(charges[1]["amount"], Decimal("180.00"))
        self.assertEqual(preview["total"], Decimal("300.00"))

    def test_a_gap_still_starts_a_new_line(self):
        """The horse leaves and comes back — the days must match the dates."""
        self.placement.end_date = date(2026, 8, 10)
        self.placement.save()
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.field_b,
            rate_type=self.rate, start_date=date(2026, 9, 1),
        )

        preview = InvoiceService.calculate_invoice_preview(self.owner, *PERIOD)
        charges = sorted(preview["livery_charges"], key=lambda c: c["days"])

        self.assertEqual(len(charges), 2)
        self.assertEqual(charges[0]["days"], 6)   # 5-10 Aug
        self.assertEqual(charges[1]["days"], 8)   # 1-8 Sep
        self.assertEqual(preview["total"], Decimal("84.00"))

    def test_same_price_under_a_different_rate_type_is_not_merged(self):
        """Rate type names are printed, so a rename must not be hidden."""
        same_price = RateType.objects.create(
            name="Grass Livery incl hay", daily_rate=Decimal("6.00")
        )
        self._move(self.field_b, date(2026, 9, 4), rate_type=same_price)

        preview = InvoiceService.calculate_invoice_preview(self.owner, *PERIOD)
        self.assertEqual(len(preview["livery_charges"]), 2)
        self.assertEqual(preview["total"], Decimal("210.00"))


class CoOwnedMergeTests(TestCase):
    """Merged runs must still split to the penny across co-owners."""

    def setUp(self):
        self.alice = Owner.objects.create(name="Alice", email="a@example.com")
        self.bob = Owner.objects.create(name="Bob", email="b@example.com")
        self.field_a = Location.objects.create(site="Colgate", name="Top Field")
        self.field_b = Location.objects.create(site="Colgate", name="Mid Field")
        self.rate = RateType.objects.create(
            name="Horse grazing incl hay", daily_rate=Decimal("7.00")
        )
        self.horse = Horse.objects.create(name="Trio")
        OwnershipShare.objects.create(
            horse=self.horse, owner=self.alice,
            share_percentage=Decimal("33.33"), is_primary_contact=True,
        )
        OwnershipShare.objects.create(
            horse=self.horse, owner=self.bob, share_percentage=Decimal("66.67"),
        )
        Placement.objects.create(
            horse=self.horse, owner=self.alice, location=self.field_a,
            rate_type=self.rate, start_date=date(2026, 8, 5),
        )
        PlacementService.move_horse(
            self.horse, new_location=self.field_b, move_date=date(2026, 8, 7),
        )

    def test_each_co_owner_gets_one_merged_line(self):
        for owner in (self.alice, self.bob):
            charges = InvoiceService.calculate_livery_charges(owner, *PERIOD)
            self.assertEqual(len(charges), 1, owner.name)
            self.assertEqual(charges[0]["days"], 35)

    def test_the_split_still_sums_to_the_full_charge(self):
        # 35 days x £7.00 = £245.00.
        billed = sum(
            InvoiceService.calculate_livery_charges(owner, *PERIOD)[0]["amount"]
            for owner in (self.alice, self.bob)
        )
        self.assertEqual(billed, Decimal("245.00"))


class OwnershipChangeTests(TestCase):
    """A move to a new owner must keep the two owners' days apart."""

    def setUp(self):
        self.old = Owner.objects.create(name="Old Owner", email="o@example.com")
        self.new = Owner.objects.create(name="New Owner", email="n@example.com")
        self.field_a = Location.objects.create(site="Colgate", name="Top Field")
        self.field_b = Location.objects.create(site="Colgate", name="Mid Field")
        self.rate = RateType.objects.create(
            name="Horse grazing incl hay", daily_rate=Decimal("6.00")
        )
        self.horse = Horse.objects.create(name="Mal De Mer")
        Placement.objects.create(
            horse=self.horse, owner=self.old, location=self.field_a,
            rate_type=self.rate, start_date=date(2026, 8, 5),
        )
        PlacementService.move_horse(
            self.horse, new_location=self.field_b, move_date=date(2026, 9, 4),
            new_owner=self.new,
        )

    def test_each_owner_is_billed_for_their_own_days(self):
        old_charges = InvoiceService.calculate_livery_charges(self.old, *PERIOD)
        new_charges = InvoiceService.calculate_livery_charges(self.new, *PERIOD)

        self.assertEqual(len(old_charges), 1)
        self.assertEqual(old_charges[0]["days"], 30)  # 5 Aug-3 Sep
        self.assertEqual(len(new_charges), 1)
        self.assertEqual(new_charges[0]["days"], 5)   # 4-8 Sep
