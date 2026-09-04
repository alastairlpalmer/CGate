"""Free text with markup-like characters must not break the PDF, and an
invoice whose PDF cannot be built must not be emailed or marked sent."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.test import TestCase
from django.utils import timezone

from billing.models import ExtraCharge
from core.models import Horse, Location, Owner, Placement, RateType
from invoicing.pdf import generate_invoice_pdf, generate_owner_statement_pdf
from invoicing.services import InvoiceService, StatementService
from notifications.emails import send_invoice_email


class PdfEscapingTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(
            name="Smith & Sons <Livery>", email="s@example.com",
            address="1 High St\n<b>Unclosed",
        )
        loc = Location.objects.create(site="S", name="F")
        rate = RateType.objects.create(name="R", daily_rate=Decimal("5.00"))
        horse = Horse.objects.create(name="Ghost <3")
        end = timezone.localdate().replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
        Placement.objects.create(
            horse=horse, owner=self.owner, location=loc, rate_type=rate,
            start_date=start - timedelta(days=10),
        )
        ExtraCharge.objects.create(
            horse=horse, owner=self.owner, charge_type="vet",
            description="Vet <font> visit & follow-up", date=start,
            amount=Decimal("40.00"), split_by_ownership=False,
        )
        self.invoice = InvoiceService.create_invoice(self.owner, start, end)
        self.invoice.notes = "Pay by <b>Friday & thanks"
        self.invoice.save(update_fields=["notes"])

    def test_invoice_pdf_renders(self):
        pdf = generate_invoice_pdf(self.invoice)
        self.assertTrue(pdf.read().startswith(b"%PDF"))

    def test_statement_pdf_renders(self):
        statement = StatementService.build_owner_statement(self.owner)
        pdf = generate_owner_statement_pdf(self.owner, statement)
        self.assertTrue(pdf.read().startswith(b"%PDF"))

    def test_email_is_not_sent_when_pdf_fails(self):
        with patch("invoicing.pdf.generate_invoice_pdf", side_effect=RuntimeError("boom")):
            self.assertFalse(send_invoice_email(self.invoice))
        self.assertEqual(len(mail.outbox), 0)

    def test_email_carries_the_pdf(self):
        self.assertTrue(send_invoice_email(self.invoice))
        self.assertEqual(len(mail.outbox[0].attachments), 1)
