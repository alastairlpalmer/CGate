"""The Django admin cannot delete an issued invoice (pre-launch review).
Its payments went with it."""

from datetime import date
from decimal import Decimal

from django.contrib.admin.sites import site
from django.db.models import ProtectedError
from django.test import RequestFactory, TestCase

from core.models import Owner
from invoicing.models import Invoice, Payment


class AdminInvoiceDeleteTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        self.request = RequestFactory().get('/')
        self.request.user = get_user_model().objects.create_superuser('root', 'r@example.com', 'pw')
        owner = Owner.objects.create(name='Anna')
        self.issued = Invoice.objects.create(
            owner=owner, invoice_number='INV1', period_start=date(2026, 6, 1), period_end=date(2026, 6, 30),
            status=Invoice.Status.SENT, total=Decimal('100'),
        )
        self.draft = Invoice.objects.create(
            owner=owner, invoice_number='INV2', period_start=date(2026, 7, 1), period_end=date(2026, 7, 31),
        )
        self.admin = site._registry[Invoice]

    def test_an_issued_invoice_cannot_be_deleted(self):
        self.assertFalse(self.admin.has_delete_permission(self.request, self.issued))
        self.assertFalse(self.admin.has_delete_permission(self.request))  # no bulk delete

    def test_a_draft_can_be_deleted(self):
        self.assertTrue(self.admin.has_delete_permission(self.request, self.draft))

    def test_the_database_keeps_an_invoice_with_payments(self):
        Payment.objects.create(invoice=self.issued, amount=Decimal('10'), date=date(2026, 7, 2))
        with self.assertRaises(ProtectedError):
            self.issued.delete()
