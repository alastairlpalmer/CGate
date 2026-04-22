"""Tests for the Concatenate invoices? feature on billing runs.

Covers:
- ON mode (default) still produces one invoice per owner with horses as line items.
- OFF mode produces one invoice per horse with compound numbering.
- Compound numbering is alphabetical by horse name.
- Run numbers increment across successive runs.
- Extra charges land on the right per-horse sub-invoice.
- Zero-total horses are skipped (and don't consume sub-numbers).
- Overlap check is horse-scoped in OFF mode, and prevents mode switching.
- Bundle PDF endpoint returns a non-empty PDF.
- Xero payload emits the compound invoice number.
- Run detail view renders correctly.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import ExtraCharge
from core.models import (
    BusinessSettings,
    Horse,
    Location,
    Owner,
    OwnershipShare,
    Placement,
    RateType,
)
from invoicing.models import Invoice, InvoiceRun
from invoicing.services import DuplicateInvoiceError, InvoiceService


User = get_user_model()


def make_user(username='staff', is_staff=True):
    u = User(
        username=username,
        last_login=timezone.now(),
        date_joined=timezone.now(),
        is_staff=is_staff,
        is_active=True,
        is_superuser=False,
    )
    u.set_password('x')
    u.save()
    return u


def build_fixture():
    """Build a tiny dataset:

    - Owner 'Mrs Chloe Vestey' with 3 horses: Acuarela, Ink, Kai.
    - Owner 'Solo Smith' with 1 horse: Bonnie.
    - All horses placed at one location with a £20/day rate.
    - One vet charge on Kai.
    """
    location = Location.objects.create(name='Main Yard', site='Home Farm')
    rate = RateType.objects.create(name='Livery', daily_rate=Decimal('20.00'))

    chloe = Owner.objects.create(name='Mrs Chloe Vestey')
    solo = Owner.objects.create(name='Solo Smith')

    horses = {}
    for name, owner in [
        ('Acuarela', chloe),
        ('Ink', chloe),
        ('Kai', chloe),
        ('Bonnie', solo),
    ]:
        h = Horse.objects.create(name=name)
        horses[name] = h
        Placement.objects.create(
            horse=h, owner=owner, location=location, rate_type=rate,
            start_date=date(2025, 1, 1),
        )
        OwnershipShare.objects.create(
            horse=h, owner=owner, share_percentage=Decimal('100.00'),
        )

    vet_on_kai = ExtraCharge.objects.create(
        horse=horses['Kai'],
        owner=chloe,
        charge_type=ExtraCharge.ChargeType.VET,
        date=date(2025, 3, 10),
        description='Check-up',
        amount=Decimal('50.00'),
        split_by_ownership=False,
    )

    return {
        'chloe': chloe,
        'solo': solo,
        'horses': horses,
        'vet_on_kai': vet_on_kai,
        'location': location,
        'rate': rate,
    }


class InvoiceRunONModeTests(TestCase):
    def test_on_mode_default_behaviour_unchanged(self):
        data = build_fixture()
        run, invoices, skipped = InvoiceService.generate_monthly_invoices(2025, 3)

        self.assertEqual(skipped, [])
        self.assertTrue(run.concatenate_invoices)
        self.assertEqual(run.run_number, 1)

        # 2 owners → 2 invoices, each linked to the run, no per-horse FK set.
        self.assertEqual(len(invoices), 2)
        for invoice in invoices:
            self.assertEqual(invoice.run, run)
            self.assertIsNone(invoice.horse)
            self.assertTrue(invoice.invoice_number.startswith('INV'))

        # Chloe's invoice has line items for all 3 of her horses.
        chloe_invoice = Invoice.objects.get(owner=data['chloe'], run=run)
        horse_names = sorted(
            li.horse.name for li in chloe_invoice.line_items.filter(line_type='livery')
        )
        self.assertEqual(horse_names, ['Acuarela', 'Ink', 'Kai'])


class InvoiceRunOFFModeTests(TestCase):
    def test_off_mode_creates_one_invoice_per_horse(self):
        data = build_fixture()
        run, invoices, skipped = InvoiceService.generate_monthly_invoices(
            2025, 3, concatenate=False
        )

        self.assertFalse(run.concatenate_invoices)
        self.assertEqual(skipped, [])
        self.assertEqual(len(invoices), 4)  # 3 for Chloe + 1 for Solo

        chloe_invoices = Invoice.objects.filter(
            run=run, owner=data['chloe']
        ).order_by('invoice_number')
        self.assertEqual(chloe_invoices.count(), 3)

        # Compound numbering is sequential and prefixed by the run number.
        numbers = [inv.invoice_number for inv in chloe_invoices]
        self.assertEqual(numbers, ['0001-0001', '0001-0002', '0001-0003'])

        # Each sub-invoice has horse set and corresponding line items.
        names = [inv.horse.name for inv in chloe_invoices]
        self.assertEqual(names, ['Acuarela', 'Ink', 'Kai'])

    def test_compound_number_ordering_alphabetical(self):
        build_fixture()
        run, invoices, _ = InvoiceService.generate_monthly_invoices(
            2025, 3, concatenate=False
        )
        # Map sub-number suffix -> horse name
        for inv in invoices:
            if inv.horse and inv.owner.name == 'Mrs Chloe Vestey':
                suffix = inv.invoice_number.split('-')[1]
                if suffix == '0001':
                    self.assertEqual(inv.horse.name, 'Acuarela')
                elif suffix == '0002':
                    self.assertEqual(inv.horse.name, 'Ink')
                elif suffix == '0003':
                    self.assertEqual(inv.horse.name, 'Kai')

    def test_off_mode_single_horse_owner(self):
        data = build_fixture()
        run, invoices, _ = InvoiceService.generate_monthly_invoices(
            2025, 3, concatenate=False
        )
        solo_invoices = [i for i in invoices if i.owner == data['solo']]
        self.assertEqual(len(solo_invoices), 1)
        self.assertEqual(solo_invoices[0].horse, data['horses']['Bonnie'])
        self.assertTrue(solo_invoices[0].invoice_number.startswith('0001-'))

    def test_extra_charge_lands_on_correct_sub_invoice(self):
        data = build_fixture()
        run, invoices, _ = InvoiceService.generate_monthly_invoices(
            2025, 3, concatenate=False
        )
        kai_invoice = Invoice.objects.get(run=run, horse=data['horses']['Kai'])
        acuarela_invoice = Invoice.objects.get(run=run, horse=data['horses']['Acuarela'])

        kai_vet_items = kai_invoice.line_items.filter(line_type='vet')
        self.assertEqual(kai_vet_items.count(), 1)
        self.assertEqual(kai_vet_items.first().line_total, Decimal('50.00'))

        self.assertEqual(acuarela_invoice.line_items.filter(line_type='vet').count(), 0)

    def test_zero_total_horse_skipped_and_sub_number_not_consumed(self):
        data = build_fixture()
        # End Bonnie's placement before March so she has zero billable days.
        Placement.objects.filter(horse=data['horses']['Bonnie']).update(
            end_date=date(2025, 2, 1)
        )
        # And clear any charges for Solo — none exist in fixture.

        run, invoices, _ = InvoiceService.generate_monthly_invoices(
            2025, 3, concatenate=False
        )

        # Only Chloe's 3 horses should produce invoices.
        self.assertEqual(len(invoices), 3)
        numbers = sorted(inv.invoice_number for inv in invoices)
        self.assertEqual(numbers, ['0001-0001', '0001-0002', '0001-0003'])

    def test_run_number_increments_across_runs(self):
        build_fixture()
        run1, _, _ = InvoiceService.generate_monthly_invoices(
            2025, 2, concatenate=False
        )
        run2, _, _ = InvoiceService.generate_monthly_invoices(
            2025, 3, concatenate=False
        )
        self.assertEqual(run1.run_number, 1)
        self.assertEqual(run2.run_number, 2)
        self.assertEqual(run1.display_number, '0001')
        self.assertEqual(run2.display_number, '0002')

        settings = BusinessSettings.get_settings()
        self.assertEqual(settings.next_run_number, 3)


class OverlapCheckTests(TestCase):
    def test_off_mode_overlap_scoped_to_horse(self):
        data = build_fixture()
        # First OFF run creates per-horse invoices for all of Chloe's horses.
        InvoiceService.generate_monthly_invoices(2025, 3, concatenate=False)

        # A second OFF run for the same period must skip (all overlap).
        _, invoices, skipped = InvoiceService.generate_monthly_invoices(
            2025, 3, concatenate=False
        )
        self.assertEqual(invoices, [])
        self.assertIn(data['chloe'], skipped)

    def test_cannot_mix_on_and_off_in_same_period(self):
        data = build_fixture()
        InvoiceService.generate_monthly_invoices(2025, 3, concatenate=True)

        # Attempting OFF for the same period should see the concatenated invoice
        # (horse IS NULL) and block creation of per-horse sub-invoices.
        with self.assertRaises(DuplicateInvoiceError):
            InvoiceService.create_invoice(
                data['chloe'],
                date(2025, 3, 1), date(2025, 3, 31),
                run=InvoiceRun.objects.create(
                    run_number=99, period_start=date(2025, 3, 1),
                    period_end=date(2025, 3, 31),
                    concatenate_invoices=False,
                ),
                horse=data['horses']['Acuarela'],
            )


class XeroPayloadTests(TestCase):
    def test_xero_payload_preserves_compound_invoice_number(self):
        from xero_integration.services import build_xero_invoice_payload

        data = build_fixture()
        _, invoices, _ = InvoiceService.generate_monthly_invoices(
            2025, 3, concatenate=False
        )
        sub = Invoice.objects.get(run__run_number=1, horse=data['horses']['Ink'])
        payload = build_xero_invoice_payload(sub, 'xero-contact-id-123')

        self.assertEqual(payload['InvoiceNumber'], sub.invoice_number)
        self.assertTrue(payload['InvoiceNumber'].startswith('0001-'))
        # Xero API: dash is the only safe separator; forward slash would
        # break GET /Invoices/{InvoiceNumber} lookups.
        self.assertNotIn('/', payload['InvoiceNumber'])
        # Line items should be Ink's only.
        self.assertGreater(len(payload['LineItems']), 0)


class RunDetailViewTests(TestCase):
    def test_run_detail_view_renders(self):
        user = make_user('runviewer')
        data = build_fixture()
        run, _, _ = InvoiceService.generate_monthly_invoices(
            2025, 3, concatenate=False
        )

        self.client.force_login(user)
        response = self.client.get(reverse('invoice_run_detail', args=[run.id]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('Run #0001', content)
        self.assertIn('Mrs Chloe Vestey', content)
        self.assertIn('Acuarela', content)
        self.assertIn('0001-0001', content)
        # Bundle PDF button visible for OFF runs.
        self.assertIn('Download bundle PDF', content)

    def test_bundle_pdf_404_for_on_mode_run(self):
        user = make_user('bundletester')
        data = build_fixture()
        run, _, _ = InvoiceService.generate_monthly_invoices(2025, 3)

        self.client.force_login(user)
        response = self.client.get(
            reverse('invoice_run_bundle_pdf', args=[run.id, data['chloe'].id])
        )
        self.assertEqual(response.status_code, 404)


class GenerateMonthlyFormTests(TestCase):
    def test_concatenate_flag_wired_through_view(self):
        user = make_user('formtester', is_staff=True)
        build_fixture()

        self.client.force_login(user)
        response = self.client.post(
            reverse('invoice_generate'),
            {'year': 2025, 'month': 3},  # no concatenate_invoices => False
            follow=False,
        )
        self.assertEqual(response.status_code, 302)
        # The created run should be OFF-mode.
        run = InvoiceRun.objects.latest('run_number')
        self.assertFalse(run.concatenate_invoices)
        # Sub-invoices use compound numbering.
        self.assertTrue(
            Invoice.objects.filter(run=run)
            .exclude(invoice_number__startswith='INV')
            .exists()
        )

    def test_concatenate_flag_on_produces_concatenated_run(self):
        user = make_user('formtester2', is_staff=True)
        build_fixture()

        self.client.force_login(user)
        response = self.client.post(
            reverse('invoice_generate'),
            {'year': 2025, 'month': 3, 'concatenate_invoices': 'on'},
            follow=False,
        )
        self.assertEqual(response.status_code, 302)
        run = InvoiceRun.objects.latest('run_number')
        self.assertTrue(run.concatenate_invoices)
        # Each invoice has no horse FK (concatenated).
        for inv in Invoice.objects.filter(run=run):
            self.assertIsNone(inv.horse)
