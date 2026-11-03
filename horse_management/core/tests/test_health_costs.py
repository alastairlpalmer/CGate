"""Vaccination and worming records can carry a cost that bills the owner —
same behaviour the farrier and vet records already had."""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import ExtraCharge
from core.models import Horse, Location, Owner, OwnershipShare, Placement, RateType
from core.roles_testutils import make_admin
from health.models import Vaccination, VaccinationType, WormingTreatment


class HealthCostTestCase(TestCase):
    def setUp(self):
        self.today = timezone.now().date()
        self.owner = Owner.objects.create(name='Jo Bloggs')
        self.location = Location.objects.create(name='Top Field', site='Main')
        self.rate = RateType.objects.create(name='Full', daily_rate=10)
        self.horse = Horse.objects.create(name='HUELLA')
        OwnershipShare.objects.create(
            horse=self.horse, owner=self.owner,
            share_percentage=100, is_primary_contact=True,
        )
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate, start_date=self.today - timedelta(days=30),
        )
        self.vax_type = VaccinationType.objects.create(
            name='Flu', interval_months=6,
        )
        self.client.force_login(make_admin(username='cost-admin'))

    def _add_vaccination(self, cost):
        return self.client.post(reverse('vaccination_create'), {
            'horse': self.horse.pk,
            'vaccination_type': self.vax_type.pk,
            'date_given': self.today.isoformat(),
            'next_due_date': '',
            'vet': '',
            'batch_number': '',
            'cost': cost,
            'notes': '',
        })

    def test_vaccination_with_cost_creates_owner_charge(self):
        response = self._add_vaccination('45.00')
        self.assertEqual(response.status_code, 302)
        vax = Vaccination.objects.get(horse=self.horse)
        self.assertEqual(vax.cost, Decimal('45.00'))
        charge = vax.extra_charge
        self.assertIsNotNone(charge)
        self.assertEqual(charge.owner, self.owner)
        self.assertEqual(charge.amount, Decimal('45.00'))
        self.assertEqual(charge.charge_type, 'vaccination')
        self.assertIn('Flu', charge.description)

    def test_vaccination_with_zero_cost_creates_no_charge(self):
        self._add_vaccination('0')
        vax = Vaccination.objects.get(horse=self.horse)
        self.assertIsNone(vax.extra_charge)
        self.assertFalse(ExtraCharge.objects.exists())

    def test_vaccination_update_syncs_uninvoiced_charge(self):
        self._add_vaccination('45.00')
        vax = Vaccination.objects.get(horse=self.horse)
        response = self.client.post(
            reverse('vaccination_update', args=[vax.pk]), {
                'horse': self.horse.pk,
                'vaccination_type': self.vax_type.pk,
                'date_given': self.today.isoformat(),
                'next_due_date': '',
                'vet': '',
                'batch_number': '',
                'cost': '60.00',
                'notes': '',
            },
        )
        self.assertEqual(response.status_code, 302)
        vax.refresh_from_db()
        self.assertEqual(vax.extra_charge.amount, Decimal('60.00'))

    def test_vaccination_update_adds_charge_when_cost_first_set(self):
        self._add_vaccination('0')
        vax = Vaccination.objects.get(horse=self.horse)
        self.client.post(reverse('vaccination_update', args=[vax.pk]), {
            'horse': self.horse.pk,
            'vaccination_type': self.vax_type.pk,
            'date_given': self.today.isoformat(),
            'next_due_date': '',
            'vet': '',
            'batch_number': '',
            'cost': '30.00',
            'notes': '',
        })
        vax.refresh_from_db()
        self.assertIsNotNone(vax.extra_charge)
        self.assertEqual(vax.extra_charge.amount, Decimal('30.00'))

    def test_worming_with_cost_creates_medication_charge(self):
        response = self.client.post(reverse('worming_create'), {
            'horse': self.horse.pk,
            'date': self.today.isoformat(),
            'product_name': 'Equest',
            'active_ingredient': '',
            'dose': '',
            'administered_by': '',
            'cost': '15.50',
            'notes': '',
        })
        self.assertEqual(response.status_code, 302)
        treatment = WormingTreatment.objects.get(horse=self.horse)
        charge = treatment.extra_charge
        self.assertIsNotNone(charge)
        self.assertEqual(charge.amount, Decimal('15.50'))
        self.assertEqual(charge.charge_type, 'medication')
        self.assertIn('Equest', charge.description)

    def test_bulk_vaccination_with_cost_charges_each_horse(self):
        horse2 = Horse.objects.create(name='TRUE506')
        OwnershipShare.objects.create(
            horse=horse2, owner=self.owner,
            share_percentage=100, is_primary_contact=True,
        )
        response = self.client.post(reverse('bulk_health_apply'), {
            'action_type': 'vaccination',
            'horse_ids': [self.horse.pk, horse2.pk],
            'vaccination_type': self.vax_type.pk,
            'date_given': self.today.isoformat(),
            'next_due_date': '',
            'vet': '',
            'batch_number': '',
            'cost': '45.00',
            'notes': '',
        })
        self.assertEqual(response.status_code, 204)
        self.assertEqual(Vaccination.objects.count(), 2)
        self.assertEqual(
            ExtraCharge.objects.filter(charge_type='vaccination').count(), 2
        )

    def test_invoiced_charge_not_touched_by_update(self):
        self._add_vaccination('45.00')
        vax = Vaccination.objects.get(horse=self.horse)
        ExtraCharge.objects.filter(pk=vax.extra_charge.pk).update(invoiced=True)
        self.client.post(reverse('vaccination_update', args=[vax.pk]), {
            'horse': self.horse.pk,
            'vaccination_type': self.vax_type.pk,
            'date_given': self.today.isoformat(),
            'next_due_date': '',
            'vet': '',
            'batch_number': '',
            'cost': '99.00',
            'notes': '',
        })
        vax.refresh_from_db()
        self.assertEqual(vax.extra_charge.amount, Decimal('45.00'))


class RecordChargeFollowsTheRecordTests(HealthCostTestCase):
    """The charge behind a health record follows edits made in either place."""

    def _update_vaccination(self, vax, **changes):
        data = {
            'horse': vax.horse_id,
            'vaccination_type': self.vax_type.pk,
            'date_given': self.today.isoformat(),
            'next_due_date': '',
            'vet': '',
            'batch_number': '',
            'cost': str(vax.cost),
            'notes': '',
        }
        data.update(changes)
        return self.client.post(reverse('vaccination_update', args=[vax.pk]), data)

    def _second_horse(self, owner=None):
        horse = Horse.objects.create(name='BRAMBLE')
        if owner:
            OwnershipShare.objects.create(
                horse=horse, owner=owner, share_percentage=100, is_primary_contact=True,
            )
            Placement.objects.create(
                horse=horse, owner=owner, location=self.location,
                rate_type=self.rate, start_date=self.today - timedelta(days=30),
            )
        return horse

    def test_moving_the_record_to_another_horse_moves_its_charge(self):
        bob = Owner.objects.create(name='Bob Brown')
        bramble = self._second_horse(owner=bob)
        self._add_vaccination('50.00')
        vax = Vaccination.objects.get(horse=self.horse)
        self.assertEqual(self._update_vaccination(vax, horse=bramble.pk).status_code, 302)
        charge = ExtraCharge.objects.get()
        self.assertEqual((charge.horse, charge.owner), (bramble, bob))
        self.assertEqual(charge.amount, Decimal('50.00'))

    def test_moving_the_record_to_a_horse_with_no_owner_removes_the_charge(self):
        bramble = self._second_horse()
        self._add_vaccination('50.00')
        vax = Vaccination.objects.get(horse=self.horse)
        self._update_vaccination(vax, horse=bramble.pk)
        vax.refresh_from_db()
        self.assertIsNone(vax.extra_charge)
        self.assertFalse(ExtraCharge.objects.exists())

    def test_a_discount_on_the_charge_screen_survives_a_record_edit(self):
        self._add_vaccination('100.00')
        vax = Vaccination.objects.get(horse=self.horse)
        charge = vax.extra_charge
        response = self.client.post(reverse('charge_update', args=[charge.pk]), {
            'horse': self.horse.pk,
            'owner': self.owner.pk,
            'service_provider': '',
            'charge_type': charge.charge_type,
            'date': charge.date.isoformat(),
            'description': charge.description,
            'amount': '80.00',
            'notes': '',
        })
        self.assertEqual(response.status_code, 302)
        vax.refresh_from_db()
        self.assertEqual(vax.cost, Decimal('80.00'))
        # An unrelated edit to the record keeps the discounted amount.
        self._update_vaccination(vax, notes='Booster due in spring')
        charge.refresh_from_db()
        self.assertEqual(charge.amount, Decimal('80.00'))
