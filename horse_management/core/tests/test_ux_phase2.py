"""One main button per page (docs/UX_REVIEW_PLAN.md, phase 2).

The invoice, horse and owner pages each lead with one main button. Their
other actions are in a More menu, with the destructive ones last. The
owner page leads with what the owner owes.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from billing.models import ExtraCharge
from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin, make_user_with_access
from invoicing.models import Invoice, Payment
from invoicing.services import InvoiceService

AUGUST = (date(2026, 8, 1), date(2026, 8, 31))


class PhaseTwoTestCase(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name='Roz Tobin', email='roz@example.com')
        self.location = Location.objects.create(site='Colgate', name='Top')
        rate = RateType.objects.create(name='Grass', daily_rate=Decimal('5.00'))
        self.horse = Horse.objects.create(name='Ida')
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=rate, start_date=date(2026, 7, 23),
        )
        self.invoice = InvoiceService.create_invoice(self.owner, *AUGUST)
        self.client.force_login(make_admin())

    def page(self, name, pk):
        return self.client.get(reverse(name, args=[pk])).content.decode()

    @staticmethod
    def header(body):
        """The page header only: everything before the first card."""
        return body.split('<div class="page-header">', 1)[1].split('class="card', 1)[0]


class InvoiceHeaderTests(PhaseTwoTestCase):
    def test_a_draft_leads_with_send(self):
        header = self.header(self.page('invoice_detail', self.invoice.pk))
        self.assertEqual(header.count('class="btn-primary"'), 1)
        self.assertIn('Send Invoice', header.split('x-data="moreMenu"')[0])
        self.assertIn('Delete Draft', header.split('x-data="moreMenu"')[1])

    def test_a_sent_invoice_leads_with_record_payment(self):
        Invoice.objects.filter(pk=self.invoice.pk).update(status=Invoice.Status.SENT)
        header = self.header(self.page('invoice_detail', self.invoice.pk))
        self.assertEqual(header.count('class="btn-primary"'), 1)
        outside = header.split('x-data="moreMenu"')[0]
        self.assertIn('Record Payment', outside)
        self.assertNotIn('Send Invoice', header)
        self.assertIn('Mark as Paid', header.split('x-data="moreMenu"')[1])

    def test_a_paid_invoice_leads_with_the_pdf(self):
        Invoice.objects.filter(pk=self.invoice.pk).update(status=Invoice.Status.PAID)
        header = self.header(self.page('invoice_detail', self.invoice.pk))
        self.assertIn('class="btn-primary" hx-boost="false">', header)
        self.assertNotIn('Record Payment', header)


class HorseHeaderTests(PhaseTwoTestCase):
    def test_move_leads_and_depart_is_in_more(self):
        body = self.page('horse_detail', self.horse.pk)
        before_menu, menu = body.split('x-data="moreMenu"', 1)
        self.assertIn('data-popup-title="Move Ida" class="btn-primary"', before_menu)
        self.assertIn('Depart…', menu.split('</nav>', 1)[0])

    def test_the_record_tiles_are_one_style(self):
        body = self.page('horse_detail', self.horse.pk)
        self.assertNotIn('bg-error-red-50 flex items-center justify-center', body)
        self.assertIn('id="quick-actions"', body)


class OwnerSummaryTests(PhaseTwoTestCase):
    def setUp(self):
        super().setUp()
        Invoice.objects.filter(pk=self.invoice.pk).update(status=Invoice.Status.SENT)
        self.invoice.refresh_from_db()
        Payment.objects.create(
            invoice=self.invoice, date=date(2026, 9, 5), amount=Decimal('20.00'),
        )
        ExtraCharge.objects.create(
            horse=self.horse, owner=self.owner, charge_type='vet',
            date=date(2026, 9, 10), description='Checkup',
            amount=Decimal('1250.00'), split_by_ownership=False,
        )

    def test_the_page_leads_with_money(self):
        body = self.page('owner_detail', self.owner.pk)
        strip = body.split('class="summary-strip"', 1)[1].split('<!-- Contact', 1)[0]
        owed = self.invoice.total - Decimal('20.00')
        self.assertIn(f'£{owed:,.2f}', strip)
        self.assertIn('£1,250.00', strip)
        self.assertIn('£20.00', strip)
        self.assertIn('5 Sep 2026', strip)

    def test_money_is_hidden_without_access(self):
        user = make_user_with_access('owners_only', owners='view')
        self.client.force_login(user)
        body = self.page('owner_detail', self.owner.pk)
        strip = body.split('class="summary-strip"', 1)[1].split('<!-- Contact', 1)[0]
        self.assertNotIn('Outstanding', strip)
        self.assertNotIn('Unbilled charges', strip)
        self.assertIn('Horses', strip)

    def test_archive_and_erase_are_in_more(self):
        body = self.page('owner_detail', self.owner.pk)
        menu = body.split('x-data="moreMenu"', 1)[1].split('class="summary-strip"', 1)[0]
        self.assertIn('Personal data', menu)
        self.assertIn('Archive owner', menu)


class ChargeRowTests(PhaseTwoTestCase):
    def test_delete_is_in_the_row_menu(self):
        charge = ExtraCharge.objects.create(
            horse=self.horse, owner=self.owner, charge_type='vet',
            date=date(2026, 9, 10), description='Checkup',
            amount=Decimal('50.00'), split_by_ownership=False,
        )
        body = self.client.get(reverse('charge_list')).content.decode()
        delete_url = reverse('charge_delete', args=[charge.pk])
        for chunk in body.split(f'href="{delete_url}"')[1:]:
            self.assertTrue(chunk.lstrip().startswith('role="menuitem"'))
        self.assertIn('Export all charges (CSV)', body)
