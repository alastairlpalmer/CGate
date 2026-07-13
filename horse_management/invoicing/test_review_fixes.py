"""Regression tests for the 2026-07 codebase-review fixes.

Covers (numbering matches CODEBASE_REVIEW.md, Part 1):
  #1/#8 Xero API push must bill the owner's fractional share (not days x full
        rate) and post to the sales GL account, not the owner's customer code.
  #2    Split extra charges on horses with no OwnershipShare must fall back to
        billing 100% to the charge owner instead of never being billed.
  #3    Cancelling an invoice must release its extra charges so a replacement
        invoice bills them; live-invoice line items still block re-billing.
  #4    Viewers must not reach placement-ending bulk actions.
  #5    quick_add_vet must escape the provider name.
  #6    The Costs page unbilled KPI must not double-count split charges.
  #7    yard_cost_duplicate must not mutate on GET.
  #10   CSV exports must not include cancelled (or, by default, draft) invoices.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from billing.models import ExtraCharge, YardCost
from core.models import Horse, Location, Owner, OwnershipShare, Placement, RateType
from invoicing.models import Invoice
from invoicing.services import InvoiceService
from xero_integration.services import build_xero_invoice_payload

PERIOD = (date(2026, 6, 1), date(2026, 6, 30))  # 30-day month
NEXT_PERIOD = (date(2026, 7, 1), date(2026, 7, 31))


def _make_livery(owner, horse_name="Ghost", daily_rate="5.00", shares=None):
    loc = Location.objects.create(site="Colgate", name=f"Field-{horse_name}")
    rate = RateType.objects.create(name=f"Rate-{horse_name}", daily_rate=Decimal(daily_rate))
    horse = Horse.objects.create(name=horse_name)
    for share_owner, pct in (shares or []):
        OwnershipShare.objects.create(
            horse=horse, owner=share_owner, share_percentage=Decimal(pct)
        )
    Placement.objects.create(
        horse=horse, owner=owner, location=loc,
        rate_type=rate, start_date=date(2026, 5, 1),
    )
    return horse


class XeroApiPayloadTests(TestCase):
    """#1/#8 — the API push must match the fixed CSV mapping."""

    def setUp(self):
        self.o1 = Owner.objects.create(name="Alice", email="a@example.com", account_code="SMITH01")
        self.o2 = Owner.objects.create(name="Bob", email="b@example.com")
        self.horse = _make_livery(
            self.o1, "Trio", "7.00",
            shares=[(self.o1, "50.00"), (self.o2, "50.00")],
        )

    def test_api_line_uses_split_amount_not_days_times_rate(self):
        invoice = InvoiceService.create_invoice(self.o1, *PERIOD)
        livery = invoice.line_items.get(line_type="livery")
        self.assertEqual(livery.line_total, Decimal("105.00"))  # 50% of 30x£7

        payload = build_xero_invoice_payload(invoice, "contact-id")
        line = next(l for l in payload["LineItems"] if "Rate-Trio" in l["Description"])
        # Xero computes each line as Quantity x UnitAmount.
        amount = Decimal(line["Quantity"]) * Decimal(line["UnitAmount"])
        self.assertEqual(amount, Decimal("105.00"))

    def test_api_lines_sum_to_invoice_total(self):
        ExtraCharge.objects.create(
            horse=self.horse, owner=self.o1, charge_type="vet",
            date=date(2026, 6, 10), description="Checkup",
            amount=Decimal("50.00"), split_by_ownership=False,
        )
        invoice = InvoiceService.create_invoice(self.o1, *PERIOD)
        payload = build_xero_invoice_payload(invoice, "contact-id")
        line_sum = sum(
            Decimal(l["Quantity"]) * Decimal(l["UnitAmount"])
            for l in payload["LineItems"]
        )
        self.assertEqual(line_sum, invoice.total)

    def test_owner_account_code_is_reference_not_gl_account(self):
        invoice = InvoiceService.create_invoice(self.o1, *PERIOD)
        payload = build_xero_invoice_payload(invoice, "contact-id")
        self.assertEqual(payload["Reference"], "SMITH01")
        for line in payload["LineItems"]:
            self.assertEqual(line["AccountCode"], "200")


class SharelessSplitChargeTests(TestCase):
    """#2 — split charges on share-less horses must bill the charge owner."""

    def setUp(self):
        self.owner = Owner.objects.create(name="Emma", email="e@example.com")
        self.horse = _make_livery(self.owner, "Ghost", "5.00", shares=[])
        self.charge = ExtraCharge.objects.create(
            horse=self.horse, owner=self.owner, charge_type="vet",
            date=date(2026, 6, 10), description="Stitches",
            amount=Decimal("120.00"), split_by_ownership=True,
        )

    def test_split_charge_on_shareless_horse_is_billed_in_full(self):
        preview = InvoiceService.calculate_invoice_preview(self.owner, *PERIOD)
        # £150 livery + £120 vet charge — previously the vet charge vanished.
        self.assertEqual(preview["total"], Decimal("270.00"))

    def test_charge_marked_invoiced_after_billing(self):
        invoice = InvoiceService.create_invoice(self.owner, *PERIOD)
        self.charge.refresh_from_db()
        self.assertTrue(self.charge.invoiced)
        self.assertEqual(self.charge.invoice, invoice)

    def test_owner_with_only_shareless_split_charge_is_billed(self):
        # No placement at all: the charge alone must pull the owner into the
        # monthly billing run.
        lone_owner = Owner.objects.create(name="Lone", email="l@example.com")
        lone_horse = Horse.objects.create(name="Solo")
        ExtraCharge.objects.create(
            horse=lone_horse, owner=lone_owner, charge_type="farrier",
            date=date(2026, 6, 5), description="Trim",
            amount=Decimal("40.00"), split_by_ownership=True,
        )
        owners = InvoiceService.get_owners_for_billing(*PERIOD)
        self.assertIn(lone_owner, list(owners))

    def test_shared_horse_split_still_splits(self):
        # The fallback must not swallow charges on horses that DO have shares.
        o2 = Owner.objects.create(name="Bob", email="b@example.com")
        shared = _make_livery(
            self.owner, "Duo", "6.00",
            shares=[(self.owner, "50.00"), (o2, "50.00")],
        )
        ExtraCharge.objects.create(
            horse=shared, owner=self.owner, charge_type="feed",
            date=date(2026, 6, 8), description="Hay",
            amount=Decimal("60.00"), split_by_ownership=True,
        )
        preview = InvoiceService.calculate_invoice_preview(o2, *PERIOD)
        feed = [c for c in preview["extra_charges"] if c["line_type"] == "feed"]
        self.assertEqual(len(feed), 1)
        self.assertEqual(feed[0]["amount"], Decimal("30.00"))


class CancelReleasesChargesTests(TestCase):
    """#3 — cancelling an invoice frees its charges for re-billing."""

    def setUp(self):
        self.staff = User.objects.create_user("admin", password="pw", is_staff=True)
        self.owner = Owner.objects.create(name="Emma", email="e@example.com")
        self.horse = _make_livery(self.owner, "Ghost", "5.00", shares=[])
        self.charge = ExtraCharge.objects.create(
            horse=self.horse, owner=self.owner, charge_type="vet",
            date=date(2026, 6, 10), description="Stitches",
            amount=Decimal("120.00"), split_by_ownership=False,
        )

    def _cancel_via_view(self, invoice):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("invoice_update", args=[invoice.pk]),
            {
                "status": Invoice.Status.CANCELLED,
                "payment_terms_days": invoice.payment_terms_days,
                "due_date": invoice.due_date.isoformat(),
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.CANCELLED)

    def test_cancel_releases_charge_and_replacement_bills_it(self):
        invoice = InvoiceService.create_invoice(self.owner, *PERIOD)
        self.assertEqual(invoice.total, Decimal("270.00"))
        self.charge.refresh_from_db()
        self.assertTrue(self.charge.invoiced)

        self._cancel_via_view(invoice)

        self.charge.refresh_from_db()
        self.assertFalse(self.charge.invoiced)
        self.assertIsNone(self.charge.invoice)

        replacement = InvoiceService.create_invoice(self.owner, *PERIOD)
        # Previously the replacement was £150 — the £120 charge was stranded.
        self.assertEqual(replacement.total, Decimal("270.00"))

    def test_unbilled_total_restored_after_cancel(self):
        invoice = InvoiceService.create_invoice(self.owner, *PERIOD)
        self.assertEqual(ExtraCharge.unbilled_total(), Decimal("0.00"))
        self._cancel_via_view(invoice)
        self.assertEqual(ExtraCharge.unbilled_total(), Decimal("120.00"))

    def test_split_charge_cancel_only_rebills_cancelled_owner(self):
        # Co-owned horse: bill both owners, cancel one, and confirm only the
        # cancelled owner's share is offered for re-billing.
        o2 = Owner.objects.create(name="Bob", email="b@example.com")
        shared = _make_livery(
            self.owner, "Duo", "6.00",
            shares=[(self.owner, "50.00"), (o2, "50.00")],
        )
        split = ExtraCharge.objects.create(
            horse=shared, owner=self.owner, charge_type="feed",
            date=date(2026, 6, 8), description="Hay",
            amount=Decimal("80.00"), split_by_ownership=True,
        )
        inv1 = InvoiceService.create_invoice(self.owner, *PERIOD)
        inv2 = InvoiceService.create_invoice(o2, *PERIOD)
        split.refresh_from_db()
        self.assertTrue(split.invoiced)

        self._cancel_via_view(inv2)
        split.refresh_from_db()
        self.assertFalse(split.invoiced)

        # The still-live owner must NOT be offered the charge again...
        preview1 = InvoiceService.calculate_invoice_preview(self.owner, *NEXT_PERIOD)
        feed1 = [c for c in preview1["extra_charges"] if c["line_type"] == "feed"]
        self.assertEqual(feed1, [])
        # ...but the cancelled owner must be, at their 50% share.
        preview2 = InvoiceService.calculate_invoice_preview(o2, *NEXT_PERIOD)
        feed2 = [c for c in preview2["extra_charges"] if c["line_type"] == "feed"]
        self.assertEqual(len(feed2), 1)
        self.assertEqual(feed2[0]["amount"], Decimal("40.00"))

    def test_billed_co_owner_not_rebilled_next_period(self):
        # While a split charge waits for its other co-owner, the already
        # billed owner must not be re-billed in a later period.
        o2 = Owner.objects.create(name="Bob", email="b@example.com")
        shared = _make_livery(
            self.owner, "Duo", "6.00",
            shares=[(self.owner, "50.00"), (o2, "50.00")],
        )
        ExtraCharge.objects.create(
            horse=shared, owner=self.owner, charge_type="feed",
            date=date(2026, 6, 8), description="Hay",
            amount=Decimal("80.00"), split_by_ownership=True,
        )
        InvoiceService.create_invoice(self.owner, *PERIOD)
        preview = InvoiceService.calculate_invoice_preview(self.owner, *NEXT_PERIOD)
        feed = [c for c in preview["extra_charges"] if c["line_type"] == "feed"]
        self.assertEqual(feed, [])


class ViewerPermissionTests(TestCase):
    """#4 — viewers must not end placements via the bulk endpoint."""

    def setUp(self):
        self.viewer = User.objects.create_user("viewer", password="pw", is_staff=False)
        self.staff = User.objects.create_user("admin", password="pw", is_staff=True)
        self.owner = Owner.objects.create(name="Emma", email="e@example.com")
        self.horse = _make_livery(self.owner, "Ghost", "5.00", shares=[])

    def _apply_departure(self):
        return self.client.post(
            reverse("bulk_health_apply"),
            {
                "action_type": "actual_departure",
                "horse_ids": [self.horse.pk],
                "date": "2026-06-15",
            },
        )

    def test_viewer_blocked_from_departure_action(self):
        self.client.force_login(self.viewer)
        response = self._apply_departure()
        self.assertEqual(response.status_code, 403)
        self.horse.refresh_from_db()
        self.assertTrue(self.horse.is_active)
        self.assertIsNone(self.horse.placements.first().end_date)

    def test_staff_can_still_apply_departure(self):
        self.client.force_login(self.staff)
        response = self._apply_departure()
        self.assertEqual(response.status_code, 204)
        self.horse.refresh_from_db()
        self.assertFalse(self.horse.is_active)


class QuickAddVetEscapingTests(TestCase):
    """#5 — provider names must be HTML-escaped in the HTMX fragment."""

    def test_malicious_name_is_escaped(self):
        user = User.objects.create_user("viewer", password="pw")
        self.client.force_login(user)
        payload = '</option><img src=x onerror=alert(1)>'
        response = self.client.post(reverse("quick_add_vet"), {"vet_name": payload})
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertNotIn("<img", body)
        self.assertIn("&lt;img", body)


class CostsUnbilledKpiTests(TestCase):
    """#6 — the Costs page KPI must use the reconciled unbilled total."""

    def test_partially_invoiced_split_charge_not_double_counted(self):
        staff = User.objects.create_user("admin", password="pw", is_staff=True)
        o1 = Owner.objects.create(name="Alice", email="a@example.com")
        o2 = Owner.objects.create(name="Bob", email="b@example.com")
        horse = _make_livery(o1, "Duo", "6.00", shares=[(o1, "50.00"), (o2, "50.00")])
        ExtraCharge.objects.create(
            horse=horse, owner=o1, charge_type="feed",
            date=date(2026, 6, 8), description="Hay",
            amount=Decimal("80.00"), split_by_ownership=True,
        )
        InvoiceService.create_invoice(o1, *PERIOD)  # bills Alice's £40 half

        self.client.force_login(staff)
        response = self.client.get(reverse("costs_list"))
        # Only Bob's £40 half is still unbilled — not the full £80.
        self.assertEqual(response.context["unbilled_total"], Decimal("40.00"))


class YardCostDuplicateMethodTests(TestCase):
    """#7 — duplication must require POST."""

    def setUp(self):
        self.staff = User.objects.create_user("admin", password="pw", is_staff=True)
        self.cost = YardCost.objects.create(
            category="hay", date=date(2026, 6, 1),
            description="Hay bales", amount=Decimal("200.00"),
        )

    def test_get_does_not_duplicate(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("yard_cost_duplicate", args=[self.cost.pk]))
        self.assertEqual(response.status_code, 405)
        self.assertEqual(YardCost.objects.count(), 1)

    def test_post_duplicates(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse("yard_cost_duplicate", args=[self.cost.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(YardCost.objects.count(), 2)


class CsvExportStatusTests(TestCase):
    """#10 — cancelled/draft invoices must not leak into Xero CSV exports."""

    def setUp(self):
        self.staff = User.objects.create_user("admin", password="pw", is_staff=True)
        self.owner = Owner.objects.create(name="Emma", email="e@example.com")
        _make_livery(self.owner, "Ghost", "5.00", shares=[])
        self.invoice = InvoiceService.create_invoice(self.owner, *PERIOD)

    def test_bulk_export_excludes_cancelled_and_draft(self):
        self.invoice.status = Invoice.Status.CANCELLED
        self.invoice.save(update_fields=["status"])
        draft = InvoiceService.create_invoice(self.owner, *NEXT_PERIOD)  # DRAFT

        self.client.force_login(self.staff)
        body = self.client.get(reverse("invoice_export_csv")).content.decode()
        self.assertNotIn(self.invoice.invoice_number, body)
        self.assertNotIn(draft.invoice_number, body)

    def test_bulk_export_includes_drafts_when_explicitly_requested(self):
        self.client.force_login(self.staff)
        body = self.client.get(
            reverse("invoice_export_csv"), {"status": "draft"}
        ).content.decode()
        self.assertIn(self.invoice.invoice_number, body)

    def test_single_cancelled_invoice_csv_blocked(self):
        self.invoice.status = Invoice.Status.CANCELLED
        self.invoice.save(update_fields=["status"])
        self.client.force_login(self.staff)
        response = self.client.get(reverse("invoice_csv", args=[self.invoice.pk]))
        self.assertEqual(response.status_code, 302)
