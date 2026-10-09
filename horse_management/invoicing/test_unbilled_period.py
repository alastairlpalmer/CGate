"""The create form must not bill days an invoice already covers.

Reported: an owner was invoiced up to 10 Jul 2026. To see what she owes
now, the user picked a wide range (1 Sep 2024 – 16 Oct 2026) and the
preview listed livery from months already invoiced. The only way round
it was to look up the day after the last invoice and type it in.
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from billing.models import ExtraCharge
from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin
from invoicing.models import Invoice
from invoicing.services import InvoiceService

RATE = Decimal("7.00")


class UnbilledPeriodTestCase(TestCase):

    def setUp(self):
        self.client.force_login(make_admin())
        self.owner = Owner.objects.create(name="Vittoria", email="v@example.com")
        self.loc = Location.objects.create(site="Colgate", name="Top")
        self.rate = RateType.objects.create(name="Grass", daily_rate=RATE)
        self.horse = Horse.objects.create(name="Pridie")
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.loc,
            rate_type=self.rate, start_date=date(2026, 1, 1),
        )
        # Invoiced from arrival up to 10 Jul.
        self.invoice = InvoiceService.create_invoice(
            self.owner, date(2026, 1, 1), date(2026, 7, 10)
        )
        self.farrier = ExtraCharge.objects.create(
            horse=self.horse, owner=self.owner, charge_type="farrier",
            date=date(2026, 8, 3), description="Trim Only",
            amount=Decimal("40.00"), split_by_ownership=False,
        )

    def _post(self, start, end):
        return self.client.post(reverse("invoice_create"), {
            "owner": self.owner.pk,
            "period_start": start,
            "period_end": end,
            "notes": "",
        })


class ResolvePeriodTests(UnbilledPeriodTestCase):

    def test_start_moves_past_invoiced_days(self):
        resolved = InvoiceService.resolve_period(
            self.owner, date(2024, 9, 1), date(2026, 10, 16)
        )
        self.assertIsNone(resolved["blocker"])
        self.assertEqual(resolved["period_start"], date(2026, 7, 11))
        self.assertEqual(resolved["period_end"], date(2026, 10, 16))
        self.assertEqual(resolved["invoiced"], [self.invoice])

    def test_no_overlap_keeps_period(self):
        resolved = InvoiceService.resolve_period(
            self.owner, date(2026, 8, 1), date(2026, 8, 31)
        )
        self.assertEqual(resolved["period_start"], date(2026, 8, 1))
        self.assertEqual(resolved["invoiced"], [])

    def test_earlier_gap_with_livery_blocks(self):
        # Invoice Aug only: 11–31 Jul is still unbilled livery, so a range
        # across both gaps cannot be one invoice.
        InvoiceService.create_invoice(
            self.owner, date(2026, 8, 1), date(2026, 8, 31)
        )
        resolved = InvoiceService.resolve_period(
            self.owner, date(2026, 6, 1), date(2026, 9, 30)
        )
        self.assertIsNone(resolved["period_start"])
        self.assertEqual(resolved["blocker"], self.invoice)
        self.assertEqual(resolved["unbilled_gaps"], [
            (date(2026, 7, 11), date(2026, 7, 31)),
            (date(2026, 9, 1), date(2026, 9, 30)),
        ])

    def test_invoice_covering_the_end_blocks(self):
        aug = InvoiceService.create_invoice(
            self.owner, date(2026, 8, 1), date(2026, 8, 31)
        )
        resolved = InvoiceService.resolve_period(
            self.owner, date(2026, 7, 11), date(2026, 8, 31)
        )
        self.assertEqual(resolved["blocker"], aug)

    def test_cancelled_invoice_does_not_count(self):
        self.invoice.status = Invoice.Status.CANCELLED
        self.invoice.save()
        resolved = InvoiceService.resolve_period(
            self.owner, date(2026, 1, 1), date(2026, 10, 16)
        )
        self.assertEqual(resolved["period_start"], date(2026, 1, 1))


class PreviewTests(UnbilledPeriodTestCase):

    def _preview(self, start, end, **headers):
        return self.client.get(reverse("invoice_preview"), {
            "owner": self.owner.pk,
            "period_start": start,
            "period_end": end,
        }, HTTP_HX_REQUEST="true", **headers)

    def test_preview_leaves_out_invoiced_days(self):
        response = self._preview("2024-09-01", "2026-10-16")
        preview = response.context["preview"]
        days = (date(2026, 10, 16) - date(2026, 7, 11)).days + 1
        self.assertEqual(
            preview["subtotal"], days * RATE + self.farrier.amount
        )
        self.assertContains(response, "Already invoiced up to 10 Jul 2026")
        self.assertContains(response, self.invoice.invoice_number)
        self.assertContains(response, "starts on <strong>11 Jul 2026")

    def test_blocked_preview_shows_no_total(self):
        InvoiceService.create_invoice(
            self.owner, date(2026, 8, 1), date(2026, 8, 31)
        )
        response = self._preview("2026-06-01", "2026-09-30")
        self.assertNotContains(response, "Amount Due")
        self.assertContains(response, "Make one invoice for each period")

    def test_owner_change_fills_start_after_last_invoice(self):
        response = self._preview(
            "2026-09-01", "2026-09-30", HTTP_HX_TRIGGER_NAME="owner"
        )
        content = response.content.decode()
        self.assertIn('hx-swap-oob="true"', content)
        self.assertIn('value="2026-07-11"', content)
        self.assertEqual(
            response.context["preview"]["period_start"], date(2026, 7, 11)
        )

    def test_date_change_does_not_move_inputs(self):
        response = self._preview("2026-09-01", "2026-09-30")
        self.assertNotContains(response, "hx-swap-oob")

    def test_create_page_with_owner_starts_after_last_invoice(self):
        with mock.patch(
            "invoicing.views.timezone.localdate", return_value=date(2026, 10, 9)
        ):
            response = self.client.get(
                reverse("invoice_create"), {"owner": self.owner.pk}
            )
        form = response.context["form"]
        self.assertEqual(form.initial["period_start"], date(2026, 7, 11))
        self.assertEqual(form.initial["period_end"], date(2026, 9, 30))


class CreateTests(UnbilledPeriodTestCase):

    def test_wide_range_creates_invoice_for_unbilled_days(self):
        response = self._post("2024-09-01", "2026-10-16")
        new = Invoice.objects.exclude(pk=self.invoice.pk).get()
        self.assertRedirects(response, reverse("invoice_detail", args=[new.pk]))
        self.assertEqual(new.period_start, date(2026, 7, 11))
        self.assertEqual(new.period_end, date(2026, 10, 16))
        days = (date(2026, 10, 16) - date(2026, 7, 11)).days + 1
        self.assertEqual(new.subtotal, days * RATE + self.farrier.amount)
        msgs = [str(m) for m in response.wsgi_request._messages]
        self.assertTrue(any("starts on 11/07/2026" in m for m in msgs))

    def test_fully_invoiced_range_is_still_blocked(self):
        response = self._post("2026-02-01", "2026-03-31")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already has invoice")
        self.assertEqual(Invoice.objects.count(), 1)


class SuggestedPeriodTests(UnbilledPeriodTestCase):

    def test_owner_without_invoices_keeps_period(self):
        from invoicing.views import _suggested_period
        other = Owner.objects.create(name="New", email="n@example.com")
        period = (date(2026, 9, 1), date(2026, 9, 30))
        self.assertEqual(_suggested_period(other, *period), period)

    def test_invoiced_past_today_keeps_period(self):
        from invoicing.views import _suggested_period
        future = date(2026, 7, 10) + timedelta(days=1)
        with mock.patch(
            "invoicing.views.timezone.localdate", return_value=future - timedelta(days=5)
        ):
            period = (date(2026, 6, 1), date(2026, 6, 30))
            self.assertEqual(_suggested_period(self.owner, *period), period)
