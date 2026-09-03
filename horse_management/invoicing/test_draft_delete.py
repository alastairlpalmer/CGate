"""Tests for deleting draft invoices.

The month-end task drafts an invoice for every owner. When that draft is
wrong (period, horse added late) the user must be able to remove it and
create the right one for the same period — previously nothing could be
deleted and the create form only reported the clash.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from billing.models import ExtraCharge
from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin, make_viewer
from invoicing.models import Invoice, Payment
from invoicing.services import InvoiceService
from xero_integration.models import XeroInvoiceSync

AUGUST = (date(2026, 8, 1), date(2026, 8, 31))


class DraftDeleteTestCase(TestCase):

    def setUp(self):
        self.staff = make_admin()
        self.owner = Owner.objects.create(name="Roz Tobin", email="roz@example.com")
        loc = Location.objects.create(site="Colgate", name="Top")
        rate = RateType.objects.create(name="Grass", daily_rate=Decimal("5.00"))
        self.horse = Horse.objects.create(name="Ida")
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=loc,
            rate_type=rate, start_date=date(2026, 7, 23),
        )
        self.charge = ExtraCharge.objects.create(
            horse=self.horse, owner=self.owner, charge_type="vet",
            date=date(2026, 8, 10), description="Checkup",
            amount=Decimal("50.00"), split_by_ownership=False,
        )
        # The auto-created month-end draft.
        self.draft = InvoiceService.create_invoice(self.owner, *AUGUST)
        self.charge.refresh_from_db()
        self.assertTrue(self.charge.invoiced)
        self.client.force_login(self.staff)

    def _delete(self, invoice):
        return self.client.post(reverse("invoice_delete", args=[invoice.pk]))


class DeleteDraftTests(DraftDeleteTestCase):

    def test_draft_is_deleted_and_charges_released(self):
        response = self._delete(self.draft)
        self.assertRedirects(response, reverse("invoice_list"))
        self.assertFalse(Invoice.objects.filter(pk=self.draft.pk).exists())
        self.charge.refresh_from_db()
        self.assertFalse(self.charge.invoiced)
        self.assertIsNone(self.charge.invoice)

    def test_wider_period_can_be_created_after_delete(self):
        """The reported bug: draft for 1–31 Aug blocks 23 Jul – 31 Aug."""
        wide = {
            "owner": self.owner.pk,
            "period_start": "2026-07-23",
            "period_end": "2026-08-31",
            "notes": "",
        }
        blocked = self.client.post(reverse("invoice_create"), wide)
        self.assertEqual(blocked.status_code, 200)
        self.assertContains(blocked, "already has invoice")

        self._delete(self.draft)

        created = self.client.post(reverse("invoice_create"), wide)
        replacement = Invoice.objects.get(owner=self.owner)
        self.assertRedirects(
            created, reverse("invoice_detail", args=[replacement.pk])
        )
        self.assertEqual(replacement.period_start, date(2026, 7, 23))
        # The released vet charge is on the replacement, not lost.
        self.assertTrue(
            replacement.line_items.filter(charge=self.charge).exists()
        )

    def test_get_does_not_delete(self):
        response = self.client.get(reverse("invoice_delete", args=[self.draft.pk]))
        self.assertRedirects(
            response, reverse("invoice_detail", args=[self.draft.pk])
        )
        self.assertTrue(Invoice.objects.filter(pk=self.draft.pk).exists())

    def test_viewer_cannot_delete(self):
        self.client.force_login(make_viewer())
        response = self._delete(self.draft)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Invoice.objects.filter(pk=self.draft.pk).exists())

    def test_sent_invoice_is_refused(self):
        self.draft.mark_as_sent()
        response = self._delete(self.draft)
        self.assertRedirects(
            response, reverse("invoice_detail", args=[self.draft.pk])
        )
        self.assertTrue(Invoice.objects.filter(pk=self.draft.pk).exists())
        self.charge.refresh_from_db()
        self.assertTrue(self.charge.invoiced)

    def test_draft_with_payment_is_refused(self):
        Payment.objects.create(
            invoice=self.draft, date=date(2026, 9, 1), amount=Decimal("10.00")
        )
        self.assertFalse(self.draft.can_be_deleted)
        self._delete(self.draft)
        self.assertTrue(Invoice.objects.filter(pk=self.draft.pk).exists())

    def test_draft_pushed_to_xero_is_refused(self):
        XeroInvoiceSync.objects.create(
            invoice=self.draft,
            xero_invoice_id="abc-123",
            xero_invoice_number="INV-X1",
            sync_status=XeroInvoiceSync.SyncStatus.PUSHED,
        )
        self.assertIn("Xero", self.draft.deletion_blocker)
        self._delete(self.draft)
        self.assertTrue(Invoice.objects.filter(pk=self.draft.pk).exists())


class DeleteDraftUITests(DraftDeleteTestCase):

    def test_detail_page_offers_delete_for_draft_only(self):
        url = reverse("invoice_delete", args=[self.draft.pk])
        response = self.client.get(reverse("invoice_detail", args=[self.draft.pk]))
        self.assertContains(response, url)
        self.assertContains(response, "Delete Draft")

        self.draft.mark_as_sent()
        response = self.client.get(reverse("invoice_detail", args=[self.draft.pk]))
        self.assertNotContains(response, url)

    def test_viewer_does_not_see_delete(self):
        self.client.force_login(make_viewer())
        response = self.client.get(reverse("invoice_detail", args=[self.draft.pk]))
        self.assertNotContains(response, "Delete Draft")

    def test_create_form_links_to_blocking_invoice(self):
        response = self.client.post(reverse("invoice_create"), {
            "owner": self.owner.pk,
            "period_start": "2026-07-23",
            "period_end": "2026-08-31",
            "notes": "",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["form"].overlapping_invoice, self.draft
        )
        self.assertContains(
            response, reverse("invoice_detail", args=[self.draft.pk])
        )
        self.assertContains(response, "still a draft")
