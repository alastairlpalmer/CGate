"""Add Charge page: tick several horses, get one charge per horse.

The pop-up sheet (a horse's Quick Action) keeps its single horse field.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import ExtraCharge
from core.models import Horse, Location, Owner, OwnershipShare, Placement, RateType
from core.roles_testutils import make_admin


class MultiHorseChargeTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        location = Location.objects.create(name='Top Field', site='Main')
        rate = RateType.objects.create(name='Full', daily_rate=10)
        self.jo = Owner.objects.create(name='Jo Bloggs')
        self.sam = Owner.objects.create(name='Sam Smith')
        self.payer = Owner.objects.create(name='Pat Payer')
        self.alaska = Horse.objects.create(name='Alaska')
        self.bramble = Horse.objects.create(name='Bramble')
        self.orphan = Horse.objects.create(name='Nobody')
        for horse, owner in ((self.alaska, self.jo), (self.bramble, self.sam)):
            OwnershipShare.objects.create(
                horse=horse, owner=owner, share_percentage=100, is_primary_contact=True,
            )
            Placement.objects.create(
                horse=horse, owner=owner, location=location, rate_type=rate,
                start_date=self.today - timedelta(days=30),
            )
        self.client.force_login(make_admin(username='multi-admin'))

    def _post(self, horses, **overrides):
        data = {
            'service_item': 'other',
            'horses': [h.pk for h in horses],
            'owner': '',
            'service_provider': '',
            'charge_type': 'other',
            'date': self.today.isoformat(),
            'description': 'Rug wash',
            'amount': '12.50',
            'notes': '',
        }
        data.update(overrides)
        return self.client.post(reverse('charge_create'), data)

    def test_page_shows_horse_tickboxes(self):
        response = self.client.get(reverse('charge_create'))
        self.assertContains(response, 'type="checkbox" name="horses"')
        self.assertContains(response, "Each horse&#x27;s current owner")
        self.assertNotContains(response, '<select name="horse"')

    def test_horse_query_param_pre_ticks_that_horse(self):
        response = self.client.get(reverse('charge_create') + f'?horse={self.alaska.pk}')
        self.assertEqual(response.context['form'].initial['horses'], [str(self.alaska.pk)])

    def test_blank_bill_to_bills_each_horses_owner(self):
        response = self._post([self.alaska, self.bramble])
        self.assertRedirects(response, reverse('charge_list'))
        charges = {c.horse: c for c in ExtraCharge.objects.all()}
        self.assertEqual(len(charges), 2)
        self.assertEqual(charges[self.alaska].owner, self.jo)
        self.assertEqual(charges[self.bramble].owner, self.sam)
        for charge in charges.values():
            self.assertEqual(charge.amount, Decimal('12.50'))
            self.assertEqual(charge.description, 'Rug wash')

    def test_chosen_owner_pays_for_every_horse(self):
        self._post([self.alaska, self.bramble], owner=self.payer.pk)
        self.assertEqual(
            set(ExtraCharge.objects.values_list('owner', flat=True)), {self.payer.pk},
        )
        self.assertEqual(ExtraCharge.objects.count(), 2)

    def test_horse_without_owner_needs_a_bill_to(self):
        response = self._post([self.alaska, self.orphan])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No current owner to bill for: Nobody')
        self.assertFalse(ExtraCharge.objects.exists())
        # Choosing who pays lets it through
        self._post([self.alaska, self.orphan], owner=self.payer.pk)
        self.assertEqual(ExtraCharge.objects.count(), 2)

    def test_no_horse_ticked_is_an_error(self):
        response = self._post([])
        self.assertContains(response, 'Tick at least one horse.')
        self.assertFalse(ExtraCharge.objects.exists())

    def test_inactive_horses_are_not_offered(self):
        self.bramble.is_active = False
        self.bramble.save(update_fields=['is_active'])
        response = self.client.get(reverse('charge_create'))
        self.assertNotContains(response, 'Bramble')

    def test_edit_keeps_single_horse(self):
        self._post([self.alaska])
        charge = ExtraCharge.objects.get()
        response = self.client.get(reverse('charge_update', args=[charge.pk]))
        self.assertContains(response, '<select name="horse"')
        self.assertNotContains(response, 'type="checkbox" name="horses"')
