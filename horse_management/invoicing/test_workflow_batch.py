"""Regression tests for the workflow-friction batch (CODEBASE_REVIEW.md Part 2).

Covers:
  #1 Bulk send / bulk mark-paid on the invoice list.
  #4 Health record creation returns to the horse page (or ?next=) instead of
     dumping the user on the health dashboard; "Save & add another" re-opens
     the form.
  #6 The charge form's horse→owner lookup endpoint.
"""

from datetime import date
from decimal import Decimal

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin, make_user_with_access, make_viewer
from invoicing.models import Invoice
from invoicing.services import InvoiceService

PERIOD = (date(2026, 6, 1), date(2026, 6, 30))


def _make_billed_owner(name, email, horse_name):
    owner = Owner.objects.create(name=name, email=email)
    loc = Location.objects.create(site="Colgate", name=f"Field-{horse_name}")
    rate = RateType.objects.create(name=f"Rate-{horse_name}", daily_rate=Decimal("5.00"))
    horse = Horse.objects.create(name=horse_name)
    Placement.objects.create(
        horse=horse, owner=owner, location=loc,
        rate_type=rate, start_date=date(2026, 5, 1),
    )
    return owner, horse


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class InvoiceBulkActionTests(TestCase):

    def setUp(self):
        self.staff = make_admin()
        self.viewer = make_viewer()  # invoices=view — no write access
        self.o1, _ = _make_billed_owner("Alice", "a@example.com", "Ghost")
        self.o2, _ = _make_billed_owner("Bob", "", "Thunder")  # no email
        self.inv1 = InvoiceService.create_invoice(self.o1, *PERIOD)
        self.inv2 = InvoiceService.create_invoice(self.o2, *PERIOD)

    def _post(self, action, ids, **extra):
        return self.client.post(
            reverse("invoice_bulk_action"),
            {"action": action, "invoice_ids": ids, **extra},
        )

    def test_bulk_send_sends_drafts_and_reports_missing_email(self):
        self.client.force_login(self.staff)
        response = self._post("send", [self.inv1.pk, self.inv2.pk])
        self.assertEqual(response.status_code, 302)

        self.inv1.refresh_from_db()
        self.inv2.refresh_from_db()
        self.assertEqual(self.inv1.status, Invoice.Status.SENT)
        # No email address — must stay draft, not silently vanish.
        self.assertEqual(self.inv2.status, Invoice.Status.DRAFT)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("a@example.com", mail.outbox[0].to)

    def test_bulk_send_skips_non_drafts(self):
        self.inv1.mark_as_sent()
        mail.outbox.clear()
        self.client.force_login(self.staff)
        self._post("send", [self.inv1.pk])
        self.assertEqual(len(mail.outbox), 0)

    def test_bulk_mark_paid(self):
        self.inv1.mark_as_sent()
        self.client.force_login(self.staff)
        self._post("mark_paid", [self.inv1.pk, self.inv2.pk])
        self.inv1.refresh_from_db()
        self.inv2.refresh_from_db()
        self.assertEqual(self.inv1.status, Invoice.Status.PAID)
        # Draft is not payable — skipped, not force-paid.
        self.assertEqual(self.inv2.status, Invoice.Status.DRAFT)

    def test_safe_next_redirect(self):
        self.client.force_login(self.staff)
        response = self._post(
            "mark_paid", [self.inv1.pk],
            next="/invoicing/?status=sent",
        )
        self.assertEqual(response.url, "/invoicing/?status=sent")
        # Off-site next falls back to the invoice list.
        response = self._post(
            "mark_paid", [self.inv1.pk],
            next="https://evil.example/",
        )
        self.assertEqual(response.url, reverse("invoice_list"))

    def test_viewer_forbidden(self):
        self.client.force_login(self.viewer)
        response = self._post("mark_paid", [self.inv1.pk])
        self.assertEqual(response.status_code, 403)


class HealthCreateRedirectTests(TestCase):

    def setUp(self):
        self.user = make_admin()
        self.client.force_login(self.user)
        self.owner, self.horse = _make_billed_owner("Alice", "a@example.com", "Ghost")

    def _worming_data(self):
        return {
            "horse": self.horse.pk,
            "date": "2026-06-10",
            "product_name": "Equest",
            "dose": "1 syringe",
        }

    def test_created_from_horse_page_returns_to_horse(self):
        url = reverse("worming_create") + f"?horse={self.horse.pk}"
        response = self.client.post(url, self._worming_data())
        self.assertRedirects(
            response, reverse("horse_detail", args=[self.horse.pk])
        )

    def test_explicit_next_wins(self):
        next_url = reverse("health_dashboard") + "?type=overview"
        url = reverse("worming_create") + f"?horse={self.horse.pk}&next={next_url}"
        response = self.client.post(url, self._worming_data())
        self.assertEqual(response.url, next_url)

    def test_offsite_next_ignored(self):
        url = (
            reverse("worming_create")
            + f"?horse={self.horse.pk}&next=https://evil.example/"
        )
        response = self.client.post(url, self._worming_data())
        self.assertRedirects(
            response, reverse("horse_detail", args=[self.horse.pk])
        )

    def test_no_context_falls_back_to_dashboard_tab(self):
        response = self.client.post(reverse("worming_create"), self._worming_data())
        self.assertEqual(
            response.url, reverse("health_dashboard") + "?type=worming"
        )

    def test_save_and_add_reopens_form(self):
        url = reverse("worming_create") + f"?horse={self.horse.pk}"
        response = self.client.post(url, {**self._worming_data(), "save_and_add": "1"})
        self.assertEqual(response.url, url)


class HorseOwnerLookupTests(TestCase):

    def test_returns_current_owner(self):
        # The lookup backs the charge form — it needs charges view access
        # (which the seeded Viewer role does not include).
        user = make_user_with_access("charge-viewer", charges="view")
        self.client.force_login(user)
        owner, horse = _make_billed_owner("Alice", "a@example.com", "Ghost")
        response = self.client.get(reverse("horse_owner"), {"horse": horse.pk})
        self.assertEqual(response.json(), {"owner_id": owner.pk})
        response = self.client.get(reverse("horse_owner"), {"horse": "abc"})
        self.assertEqual(response.json(), {"owner_id": None})
