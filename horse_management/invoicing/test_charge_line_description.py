"""Invoice line text for an extra charge: the type is not said twice.

A charge made from a health record already starts with its type
("Farrier - Full Set"). The invoice used to put the type in front again,
which printed "Farrier - Farrier - Full Set" on owners' invoices.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from billing.models import ExtraCharge
from core.models import Horse, Location, Owner, OwnershipShare, Placement, RateType
from health.models import FarrierVisit
from health.views import sync_record_charge
from invoicing.services import InvoiceService


def _period():
    end = timezone.localdate().replace(day=1) - timedelta(days=1)
    return end.replace(day=1), end


class LineDescriptionTests(SimpleTestCase):
    def describe(self, charge_type, description):
        return InvoiceService._charge_line_description(
            ExtraCharge(charge_type=charge_type, description=description),
        )

    def test_type_already_at_the_start_is_not_repeated(self):
        cases = [
            ('farrier', 'Farrier - Full Set', 'Farrier - Full Set'),
            ('vaccination', 'Vaccination - Flu', 'Vaccination - Flu'),
            ('farrier', 'farrier - trim', 'farrier - trim'),
        ]
        for charge_type, description, expected in cases:
            with self.subTest(description=description):
                self.assertEqual(self.describe(charge_type, description), expected)

    def test_type_is_added_when_the_description_does_not_start_with_it(self):
        cases = [
            ('farrier', 'Lost shoe refit', 'Farrier - Lost shoe refit'),
            ('vet', 'Vet - Colic call-out', 'Veterinary - Vet - Colic call-out'),
            ('transport', 'To the sales', 'Transport - To the sales'),
            # A word that only begins like the type is not the type.
            ('farrier', 'Farriery kit', 'Farrier - Farriery kit'),
            ('other', '', 'Other - '),
        ]
        for charge_type, description, expected in cases:
            with self.subTest(description=description):
                self.assertEqual(self.describe(charge_type, description), expected)


class FarrierChargeOnInvoiceTests(TestCase):
    def setUp(self):
        start, self.end = _period()
        self.start = start
        self.jo = Owner.objects.create(name='Jo Bloggs', email='jo@example.com')
        self.ali = Owner.objects.create(name='Ali Khan', email='ali@example.com')
        location = Location.objects.create(site='Main', name='Top Field')
        rate = RateType.objects.create(name='Grass', daily_rate=Decimal('5.00'))
        self.bramble = self._horse('Bramble', location, rate)
        self.echo = self._horse('Echo', location, rate, co_owner=self.ali)

    def _horse(self, name, location, rate, co_owner=None):
        horse = Horse.objects.create(name=name)
        Placement.objects.create(
            horse=horse, owner=self.jo, location=location, rate_type=rate,
            start_date=self.start - timedelta(days=10),
        )
        share = Decimal('50') if co_owner else Decimal('100')
        OwnershipShare.objects.create(horse=horse, owner=self.jo, share_percentage=share, is_primary_contact=True)
        if co_owner:
            OwnershipShare.objects.create(horse=horse, owner=co_owner, share_percentage=share)
        return horse

    def _shoe(self, horse, work, cost):
        record = FarrierVisit.objects.create(
            horse=horse, date=self.start + timedelta(days=3), work_done=work, cost=Decimal(cost),
        )
        sync_record_charge(record)

    def test_farrier_lines_say_farrier_once(self):
        self._shoe(self.bramble, 'full_set', '95')
        self._shoe(self.echo, 'front_shoes', '65')
        invoice = InvoiceService.create_invoice(self.jo, self.start, self.end)
        lines = [item.description for item in invoice.line_items.filter(charge__isnull=False)]
        self.assertEqual(len(lines), 2)
        for text in lines:
            with self.subTest(line=text):
                self.assertNotIn('Farrier - Farrier', text)
                self.assertEqual(text.count('Farrier'), 1)
        self.assertTrue(any(text.startswith('Farrier - Full Set') for text in lines))
        self.assertTrue(any('Farrier - Front Shoes' in text and 'share' in text for text in lines))
