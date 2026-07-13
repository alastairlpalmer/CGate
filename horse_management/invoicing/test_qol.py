"""Tests for the invoicing quality-of-life batch: period filter + totals on
the invoice list, mark-paid next-redirects, create-horse redirect, and
quick-find covering departed horses."""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import Horse, Location, Owner, Placement, RateType
from invoicing.models import Invoice, Payment
from invoicing.services import InvoiceService


def _invoice(owner, start, end):
    return InvoiceService.create_invoice(owner, start, end)


class InvoiceListFilterTotalsTests(TestCase):

    def setUp(self):
        self.staff = User.objects.create_user("admin", password="pw", is_staff=True)
        self.owner = Owner.objects.create(name="Alice", email="a@example.com")
        loc = Location.objects.create(site="Colgate", name="Top")
        rate = RateType.objects.create(name="Grass", daily_rate=Decimal("5.00"))
        horse = Horse.objects.create(name="Ghost")
        Placement.objects.create(
            horse=horse, owner=self.owner, location=loc,
            rate_type=rate, start_date=date(2026, 1, 1),
        )
        self.june = _invoice(self.owner, date(2026, 6, 1), date(2026, 6, 30))  # £150
        self.may = _invoice(self.owner, date(2026, 5, 1), date(2026, 5, 31))   # £155
        self.client.force_login(self.staff)

    def test_period_filter_limits_list(self):
        response = self.client.get(
            reverse("invoice_list"),
            {"date_from": "2026-06-01", "date_to": "2026-06-30"},
        )
        invoices = list(response.context["invoices"])
        self.assertEqual(invoices, [self.june])

    def test_invalid_dates_ignored(self):
        response = self.client.get(
            reverse("invoice_list"), {"date_from": "not-a-date"}
        )
        self.assertEqual(len(response.context["invoices"]), 2)

    def test_summary_totals_cover_filtered_set_with_part_payments(self):
        self.june.mark_as_sent()
        Payment.objects.create(
            invoice=self.june, date=date(2026, 7, 1), amount=Decimal("50.00")
        )
        response = self.client.get(reverse("invoice_list"))
        self.assertEqual(response.context["summary_count"], 2)
        self.assertEqual(
            response.context["summary_invoiced"], Decimal("305.00")
        )
        # June: 150 - 50 paid = 100 open; May is still a draft (unsent but
        # unpaid) so counts as outstanding too: 100 + 155.
        self.assertEqual(
            response.context["summary_outstanding"], Decimal("255.00")
        )

    def test_cancelled_excluded_from_totals(self):
        self.may.status = Invoice.Status.CANCELLED
        self.may.save(update_fields=["status"])
        response = self.client.get(reverse("invoice_list"))
        self.assertEqual(response.context["summary_count"], 1)
        self.assertEqual(
            response.context["summary_invoiced"], Decimal("150.00")
        )

    def test_paid_excluded_from_outstanding(self):
        self.june.mark_as_sent()
        self.june.mark_as_paid()
        response = self.client.get(reverse("invoice_list"))
        self.assertEqual(
            response.context["summary_outstanding"], Decimal("155.00")
        )

    def test_export_matches_filtered_list(self):
        self.june.mark_as_sent()
        self.may.mark_as_sent()
        body = self.client.get(
            reverse("invoice_export_csv"),
            {"date_from": "2026-06-01", "date_to": "2026-06-30"},
        ).content.decode()
        self.assertIn(self.june.invoice_number, body)
        self.assertNotIn(self.may.invoice_number, body)


class MarkPaidNextRedirectTests(TestCase):

    def setUp(self):
        self.staff = User.objects.create_user("admin", password="pw", is_staff=True)
        owner = Owner.objects.create(name="Alice", email="a@example.com")
        loc = Location.objects.create(site="S", name="F")
        rate = RateType.objects.create(name="R", daily_rate=Decimal("5.00"))
        horse = Horse.objects.create(name="Ghost")
        Placement.objects.create(
            horse=horse, owner=owner, location=loc,
            rate_type=rate, start_date=date(2026, 1, 1),
        )
        self.invoice = _invoice(owner, date(2026, 6, 1), date(2026, 6, 30))
        self.invoice.mark_as_sent()
        self.client.force_login(self.staff)

    def test_safe_next_honoured(self):
        response = self.client.post(
            reverse("invoice_mark_paid", args=[self.invoice.pk]), {"next": "/"}
        )
        self.assertEqual(response.url, "/")
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.Status.PAID)

    def test_offsite_next_falls_back_to_detail(self):
        response = self.client.post(
            reverse("invoice_mark_paid", args=[self.invoice.pk]),
            {"next": "https://evil.example/"},
        )
        self.assertEqual(
            response.url, reverse("invoice_detail", args=[self.invoice.pk])
        )


class HorseCreateRedirectTests(TestCase):

    def test_lands_on_new_horse_detail(self):
        staff = User.objects.create_user("admin", password="pw", is_staff=True)
        self.client.force_login(staff)
        response = self.client.post(reverse("horse_create"), {
            "name": "Dobbin",
            "color": "bay",
            "sex": "gelding",
            "ownership_shares-TOTAL_FORMS": "0",
            "ownership_shares-INITIAL_FORMS": "0",
            "ownership_shares-MIN_NUM_FORMS": "0",
            "ownership_shares-MAX_NUM_FORMS": "1000",
        })
        horse = Horse.objects.get(name="Dobbin")
        self.assertRedirects(response, reverse("horse_detail", args=[horse.pk]))


class QuickFindDepartedTests(TestCase):

    def test_departed_horse_found_and_labelled(self):
        user = User.objects.create_user("viewer", password="pw")
        self.client.force_login(user)
        Horse.objects.create(name="Vanished", is_active=False)
        response = self.client.get(reverse("quick_find"), {"q": "Vanished"})
        body = response.content.decode()
        self.assertIn("Vanished", body)
        self.assertIn("Departed", body)

    def test_active_horses_sort_before_departed(self):
        user = User.objects.create_user("viewer", password="pw")
        self.client.force_login(user)
        Horse.objects.create(name="Star One", is_active=False)
        Horse.objects.create(name="Star Two", is_active=True)
        response = self.client.get(reverse("quick_find"), {"q": "Star"})
        body = response.content.decode()
        self.assertLess(body.index("Star Two"), body.index("Star One"))
