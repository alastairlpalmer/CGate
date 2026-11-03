"""One invoice per horse (Owner.invoice_per_horse).

An owner can be billed one invoice for each horse instead of one invoice
for all their horses. The per-horse invoices for a period are made
together and share the period, so the overlap and coverage checks keep
working on the owner as a whole.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from billing.models import ExtraCharge
from core.models import Horse, Location, Owner, OwnershipShare, Placement, RateType
from core.roles_testutils import make_admin
from invoicing.models import Invoice
from invoicing.services import DuplicateInvoiceError, InvoiceService
from invoicing.utils import invoice_to_xero_rows

PERIOD = (date(2026, 6, 1), date(2026, 6, 30))  # 30 days


class PerHorseTestCase(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(
            name='Petra Price', email='petra@example.com',
            invoice_per_horse=True, invoice_reference='PO-7',
        )
        self.loc = Location.objects.create(site='Colgate', name='Top Field')
        self.rate = RateType.objects.create(name='Grass', daily_rate=Decimal('5.00'))
        # Created out of name order: the invoices come out in name order.
        self.willow = self._place('Willow')
        self.apple = self._place('Apple')
        self.vet = ExtraCharge.objects.create(
            horse=self.willow, owner=self.owner, charge_type='vet',
            date=date(2026, 6, 10), description='Check-up',
            amount=Decimal('40.00'), split_by_ownership=False,
        )

    def _place(self, name, owner=None):
        horse = Horse.objects.create(name=name)
        Placement.objects.create(
            horse=horse, owner=owner or self.owner, location=self.loc,
            rate_type=self.rate, start_date=date(2026, 5, 1),
        )
        return horse


class CreateInvoicesTests(PerHorseTestCase):
    def test_one_invoice_per_horse_in_name_order(self):
        invoices = InvoiceService.create_invoices(self.owner, *PERIOD)
        self.assertEqual([inv.horse for inv in invoices], [self.apple, self.willow])
        apple, willow = invoices
        # 30 days x £5 each; Willow also carries her vet bill.
        self.assertEqual(apple.subtotal, Decimal('150.00'))
        self.assertEqual(willow.subtotal, Decimal('190.00'))
        for invoice in invoices:
            self.assertEqual((invoice.period_start, invoice.period_end), PERIOD)
            self.assertEqual(invoice.customer_reference, 'PO-7')
            self.assertEqual(
                {item.horse for item in invoice.line_items.all()}, {invoice.horse},
            )

    def test_each_invoice_has_its_own_number(self):
        invoices = InvoiceService.create_invoices(self.owner, *PERIOD)
        numbers = [inv.invoice_number for inv in invoices]
        self.assertEqual(len(set(numbers)), 2)

    def test_the_extra_charge_is_billed_on_its_horse_invoice(self):
        invoices = InvoiceService.create_invoices(self.owner, *PERIOD)
        self.vet.refresh_from_db()
        self.assertTrue(self.vet.invoiced)
        willow_invoice = next(inv for inv in invoices if inv.horse == self.willow)
        self.assertEqual(self.vet.invoice, willow_invoice)

    def test_the_split_matches_the_one_invoice_total(self):
        preview = InvoiceService.calculate_invoice_preview(self.owner, *PERIOD)
        invoices = InvoiceService.create_invoices(self.owner, *PERIOD)
        self.assertEqual(sum(inv.subtotal for inv in invoices), preview['subtotal'])

    def test_a_horse_with_nothing_to_bill_gets_no_invoice(self):
        free = RateType.objects.create(name='Free', daily_rate=Decimal('0.00'))
        idle = Horse.objects.create(name='Bramble')
        Placement.objects.create(
            horse=idle, owner=self.owner, location=self.loc,
            rate_type=free, start_date=date(2026, 5, 1),
        )
        invoices = InvoiceService.create_invoices(self.owner, *PERIOD)
        self.assertNotIn(idle, [inv.horse for inv in invoices])
        self.assertEqual(len(invoices), 2)

    def test_the_period_cannot_be_billed_twice(self):
        InvoiceService.create_invoices(self.owner, *PERIOD)
        with self.assertRaises(DuplicateInvoiceError):
            InvoiceService.create_invoices(self.owner, *PERIOD)
        self.assertEqual(Invoice.objects.filter(owner=self.owner).count(), 2)

    def test_an_owner_without_the_setting_gets_one_invoice(self):
        self.owner.invoice_per_horse = False
        self.owner.save()
        invoices = InvoiceService.create_invoices(self.owner, *PERIOD)
        self.assertEqual(len(invoices), 1)
        self.assertIsNone(invoices[0].horse)
        self.assertEqual(invoices[0].subtotal, Decimal('340.00'))
        self.assertEqual(invoices[0].line_items.count(), 3)


class SplitChargeTests(PerHorseTestCase):
    """A co-owned horse: each co-owner is billed their share, per horse or not."""

    def test_split_charge_is_marked_invoiced_once_both_owners_are_billed(self):
        partner = Owner.objects.create(name='Quinn')
        OwnershipShare.objects.create(
            horse=self.apple, owner=self.owner, share_percentage=Decimal('50.00'),
        )
        OwnershipShare.objects.create(
            horse=self.apple, owner=partner, share_percentage=Decimal('50.00'),
        )
        shoes = ExtraCharge.objects.create(
            horse=self.apple, owner=self.owner, charge_type='farrier',
            date=date(2026, 6, 12), description='Shoes',
            amount=Decimal('80.00'), split_by_ownership=True,
        )
        invoices = InvoiceService.create_invoices(self.owner, *PERIOD)
        apple = next(inv for inv in invoices if inv.horse == self.apple)
        # Half the livery and half the shoes.
        self.assertEqual(apple.subtotal, Decimal('115.00'))
        shoes.refresh_from_db()
        self.assertFalse(shoes.invoiced)

        InvoiceService.create_invoices(partner, *PERIOD)
        shoes.refresh_from_db()
        self.assertTrue(shoes.invoiced)


class MonthlyRunTests(PerHorseTestCase):
    def test_the_monthly_run_makes_one_invoice_per_horse(self):
        other = Owner.objects.create(name='Rory')
        self._place('Comet', owner=other)
        invoices, skipped = InvoiceService.generate_monthly_invoices(2026, 6)
        mine = [inv for inv in invoices if inv.owner == self.owner]
        theirs = [inv for inv in invoices if inv.owner == other]
        self.assertEqual([inv.horse for inv in mine], [self.apple, self.willow])
        self.assertEqual(len(theirs), 1)
        self.assertIsNone(theirs[0].horse)

    def test_a_second_run_skips_an_owner_billed_per_horse(self):
        InvoiceService.generate_monthly_invoices(2026, 6)
        invoices, skipped = InvoiceService.generate_monthly_invoices(2026, 6)
        self.assertEqual(invoices, [])
        self.assertIn(self.owner, skipped)


class CreateViewTests(PerHorseTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(make_admin())

    def test_create_makes_the_invoices_and_lists_them(self):
        response = self.client.post(reverse('invoice_create'), {
            'owner': self.owner.pk,
            'period_start': '2026-06-01',
            'period_end': '2026-06-30',
            'notes': '',
        })
        self.assertRedirects(
            response, f"{reverse('invoice_list')}?owner={self.owner.pk}",
            fetch_redirect_response=False,
        )
        self.assertEqual(Invoice.objects.filter(owner=self.owner).count(), 2)
        page = self.client.get(response['Location'])
        self.assertContains(page, '2 invoices created, one per horse')
        self.assertContains(page, 'Apple')
        self.assertContains(page, 'Willow')

    def test_the_preview_says_how_many_invoices(self):
        response = self.client.get(reverse('invoice_preview'), {
            'owner': self.owner.pk,
            'period_start': '2026-06-01',
            'period_end': '2026-06-30',
        }, HTTP_HX_REQUEST='true')
        self.assertContains(response, 'This owner gets one invoice per horse')
        self.assertContains(response, 'This makes 2 invoices')

    def test_the_invoice_page_and_pdf_name_the_horse(self):
        invoice = InvoiceService.create_invoices(self.owner, *PERIOD)[0]
        page = self.client.get(reverse('invoice_detail', args=[invoice.pk]))
        self.assertContains(page, 'one invoice per horse')
        self.assertContains(page, reverse('horse_detail', args=[self.apple.pk]))
        pdf = self.client.get(reverse('invoice_pdf', args=[invoice.pk]))
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf['Content-Type'], 'application/pdf')

    def test_the_owner_form_has_the_setting(self):
        response = self.client.get(reverse('owner_update', args=[self.owner.pk]))
        self.assertContains(response, 'One invoice per horse')


class ReferenceTests(PerHorseTestCase):
    def test_the_accounting_reference_adds_the_horse(self):
        apple, _willow = InvoiceService.create_invoices(self.owner, *PERIOD)
        self.assertEqual(apple.accounting_reference, 'PO-7 / Apple')
        rows = invoice_to_xero_rows(apple)
        self.assertEqual({row['Reference'] for row in rows}, {'PO-7 / Apple'})

    def test_one_invoice_keeps_the_old_reference(self):
        self.owner.invoice_per_horse = False
        self.owner.save()
        invoice = InvoiceService.create_invoices(self.owner, *PERIOD)[0]
        self.assertEqual(invoice.accounting_reference, 'PO-7')
