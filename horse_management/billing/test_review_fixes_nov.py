"""Charges and feed defects from the November 2026 bug review.

3. Recharged feed was counted twice in the cost totals.
4. A departed horse's health record could not be edited.
5. A tiny feed-out cost over many horses crashed (negative charge).
6. A restored charge could bill an old amount.
7. Two saves at once could create two charges for one record.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import ExtraCharge, FeedOut, YardCost
from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin
from health.models import VetVisit
from health.views import sync_record_charge


class Base(TestCase):
    def setUp(self):
        self.client.force_login(make_admin())
        self.today = timezone.localdate()
        self.location = Location.objects.create(site='Colgate', name='Top Field')
        self.rate = RateType.objects.create(name='Grass', daily_rate=Decimal('5.00'))

    def _horse(self, name, owner=True):
        horse = Horse.objects.create(name=name)
        Placement.objects.create(
            horse=horse,
            owner=Owner.objects.create(name=f'Owner of {name}') if owner else None,
            location=self.location, rate_type=self.rate,
            start_date=self.today - timedelta(days=30),
        )
        return horse

    def _feed_out(self, total, horses):
        return self.client.post(reverse('feed_out_create', args=[self.location.pk]), {
            'date': self.today.isoformat(), 'feed_type': 'hay', 'quantity': '2 bales',
            'total_cost': total, 'is_recharged': 'on',
            'recharge_horses': [h.pk for h in horses],
        })


class FeedOutTests(Base):

    def test_a_tiny_cost_over_many_horses_is_shared_without_a_crash(self):
        horses = [self._horse(f'H{i}') for i in range(7)]
        response = self._feed_out('0.04', horses)
        self.assertEqual(response.status_code, 302)
        amounts = sorted(ExtraCharge.objects.values_list('amount', flat=True))
        self.assertEqual(sum(amounts), Decimal('0.04'))
        self.assertTrue(all(a >= 0 for a in amounts))

    def test_pennies_are_spread_one_each(self):
        horses = [self._horse(f'H{i}') for i in range(6)]
        self._feed_out('10.00', horses)
        amounts = sorted(ExtraCharge.objects.values_list('amount', flat=True))
        self.assertEqual(amounts, [Decimal('1.66')] * 2 + [Decimal('1.67')] * 4)

    def test_recharged_feed_is_counted_once_in_cost_totals(self):
        horses = [self._horse('A'), self._horse('B')]
        self._feed_out('500.00', horses)
        self.assertEqual(YardCost.objects.get().amount, Decimal('500.00'))
        self.assertEqual(ExtraCharge.objects.count(), 2)

        costs = self.client.get(reverse('costs_list'))
        self.assertEqual(costs.context['month_total'], Decimal('500.00'))
        finances = self.client.get(reverse('finances'))
        self.assertEqual(finances.status_code, 200)
        monthly = finances.context['chart_data']['monthly']
        self.assertEqual(monthly['costs'][monthly['forecastStart'] - 1], 500.0)


class HealthChargeTests(Base):

    def setUp(self):
        super().setUp()
        self.horse = self._horse('Ghost')
        self.visit = VetVisit.objects.create(
            horse=self.horse, date=self.today, reason='Lame', cost=Decimal('100.00'),
        )
        sync_record_charge(self.visit)
        self.charge = ExtraCharge.objects.get()

    def test_a_departed_horses_record_can_still_be_edited(self):
        self.horse.is_active = False
        self.horse.save(update_fields=['is_active'])
        response = self.client.post(reverse('vet_visit_update', args=[self.visit.pk]), {
            'horse': self.horse.pk, 'date': self.today.isoformat(),
            'reason': 'Lame', 'cost': '120.00',
        })
        self.assertEqual(response.status_code, 302)
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.amount, Decimal('120.00'))

    def test_a_restored_charge_takes_the_records_cost(self):
        self.charge.soft_delete()
        VetVisit.objects.filter(pk=self.visit.pk).update(cost=Decimal('60.00'))
        self.client.post(reverse('charge_restore', args=[self.charge.pk]))
        self.charge.refresh_from_db()
        self.assertFalse(self.charge.is_deleted)
        self.assertEqual(self.charge.amount, Decimal('60.00'))

    def test_a_charge_whose_record_now_has_no_cost_stays_deleted(self):
        self.charge.soft_delete()
        VetVisit.objects.filter(pk=self.visit.pk).update(cost=Decimal('0.00'))
        self.client.post(reverse('charge_restore', args=[self.charge.pk]))
        self.charge.refresh_from_db()
        self.assertTrue(self.charge.is_deleted)

    def test_a_sync_from_an_old_copy_makes_no_second_charge(self):
        other = VetVisit.objects.create(
            horse=self.horse, date=self.today, reason='Teeth', cost=Decimal('40.00'),
        )
        # A copy loaded before the first sync linked a charge.
        stale = VetVisit.objects.get(pk=other.pk)
        sync_record_charge(other)
        sync_record_charge(stale)
        self.assertEqual(ExtraCharge.objects.filter(amount=Decimal('40.00')).count(), 1)

    def test_an_edit_runs_inside_one_transaction_with_the_record_locked(self):
        from unittest.mock import patch

        from django.db.models.query import QuerySet

        real = QuerySet.select_for_update
        locked = []

        def spy(qs, *args, **kwargs):
            locked.append(qs.model)
            return real(qs, *args, **kwargs)

        with patch.object(QuerySet, 'select_for_update', spy):
            self.client.post(reverse('vet_visit_update', args=[self.visit.pk]), {
                'horse': self.horse.pk, 'date': self.today.isoformat(),
                'reason': 'Lame', 'cost': '100.00',
            })
        self.assertIn(VetVisit, locked)
