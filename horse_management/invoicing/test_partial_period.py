"""Regression tests: a partial-period invoice must not suppress the rest of
the month's livery.

The monthly run's idempotency used to be any-overlap-on-period, so an invoice
covering 1-10 June (e.g. settling up a departing horse) skipped the owner's
entire June — the other horses' remaining 20 days of livery were never billed
by any later run. generate_monthly_invoices now bills the uncovered remainder
of the month.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import BusinessSettings, Horse, Location, Owner, Placement, RateType
from invoicing.models import Invoice
from invoicing.services import InvoiceService


class UncoveredPeriodsTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Alice", email="alice@example.com")

    def _invoice(self, start, end):
        return Invoice.objects.create(
            owner=self.owner,
            invoice_number=f"T-{start.isoformat()}",
            period_start=start,
            period_end=end,
        )

    def test_no_existing_invoice_returns_full_period(self):
        gaps = InvoiceService.uncovered_periods(
            self.owner, date(2026, 6, 1), date(2026, 6, 30)
        )
        self.assertEqual(gaps, [(date(2026, 6, 1), date(2026, 6, 30))])

    def test_partial_invoice_leaves_remainder(self):
        self._invoice(date(2026, 6, 1), date(2026, 6, 10))
        gaps = InvoiceService.uncovered_periods(
            self.owner, date(2026, 6, 1), date(2026, 6, 30)
        )
        self.assertEqual(gaps, [(date(2026, 6, 11), date(2026, 6, 30))])

    def test_mid_month_invoice_leaves_two_gaps(self):
        self._invoice(date(2026, 6, 5), date(2026, 6, 10))
        gaps = InvoiceService.uncovered_periods(
            self.owner, date(2026, 6, 1), date(2026, 6, 30)
        )
        self.assertEqual(gaps, [
            (date(2026, 6, 1), date(2026, 6, 4)),
            (date(2026, 6, 11), date(2026, 6, 30)),
        ])

    def test_fully_covered_period_returns_no_gaps(self):
        self._invoice(date(2026, 6, 1), date(2026, 6, 30))
        self.assertEqual(
            InvoiceService.uncovered_periods(
                self.owner, date(2026, 6, 1), date(2026, 6, 30)
            ),
            [],
        )

    def test_cancelled_invoice_does_not_cover(self):
        inv = self._invoice(date(2026, 6, 1), date(2026, 6, 30))
        inv.status = Invoice.Status.CANCELLED
        inv.save(update_fields=['status'])
        gaps = InvoiceService.uncovered_periods(
            self.owner, date(2026, 6, 1), date(2026, 6, 30)
        )
        self.assertEqual(gaps, [(date(2026, 6, 1), date(2026, 6, 30))])

    def test_overlap_spanning_month_boundary(self):
        self._invoice(date(2026, 5, 20), date(2026, 6, 5))
        gaps = InvoiceService.uncovered_periods(
            self.owner, date(2026, 6, 1), date(2026, 6, 30)
        )
        self.assertEqual(gaps, [(date(2026, 6, 6), date(2026, 6, 30))])


class PartialPeriodMonthlyRunTests(TestCase):
    """The June scenario from the QA report, end to end."""

    def setUp(self):
        BusinessSettings.get_settings()
        self.owner = Owner.objects.create(name="Alice", email="alice@example.com")
        self.loc = Location.objects.create(site="Main", name="Top Field")
        self.rate = RateType.objects.create(name="Grass", daily_rate=Decimal("25.00"))
        # Horse A departed 10 June — settled up immediately with a manual
        # invoice covering 1-10 June.
        self.horse_a = Horse.objects.create(name="Early Leaver")
        Placement.objects.create(
            horse=self.horse_a, owner=self.owner, location=self.loc,
            rate_type=self.rate, start_date=date(2026, 5, 1),
            end_date=date(2026, 6, 10),
        )
        # Horse B stays all month.
        self.horse_b = Horse.objects.create(name="Stayer")
        Placement.objects.create(
            horse=self.horse_b, owner=self.owner, location=self.loc,
            rate_type=self.rate, start_date=date(2026, 5, 1),
        )
        self.manual = InvoiceService.create_invoice(
            self.owner, date(2026, 6, 1), date(2026, 6, 10)
        )

    def test_monthly_run_bills_the_uncovered_remainder(self):
        invoices, skipped = InvoiceService.generate_monthly_invoices(2026, 6)
        self.assertEqual(skipped, [])
        self.assertEqual(len(invoices), 1)
        inv = invoices[0]
        self.assertEqual(inv.period_start, date(2026, 6, 11))
        self.assertEqual(inv.period_end, date(2026, 6, 30))
        # Horse B's remaining 20 days x £25 = £500 — previously lost forever.
        self.assertEqual(inv.subtotal, Decimal("500.00"))
        # And nothing double-billed: manual invoice still covers 1-10 June
        # for both horses (10 days x £25 x 2 = £500).
        self.assertEqual(self.manual.subtotal, Decimal("500.00"))

    def test_second_run_after_gap_fill_skips_owner(self):
        InvoiceService.generate_monthly_invoices(2026, 6)
        invoices, skipped = InvoiceService.generate_monthly_invoices(2026, 6)
        self.assertEqual(invoices, [])
        self.assertEqual(skipped, [self.owner])

    def test_fully_invoiced_month_still_skips(self):
        # Cancel the partial and issue a full-month invoice instead.
        self.manual.status = Invoice.Status.CANCELLED
        self.manual.save(update_fields=['status'])
        InvoiceService.create_invoice(
            self.owner, date(2026, 6, 1), date(2026, 6, 30)
        )
        invoices, skipped = InvoiceService.generate_monthly_invoices(2026, 6)
        self.assertEqual(invoices, [])
        self.assertEqual(skipped, [self.owner])
