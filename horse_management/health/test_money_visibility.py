"""Money on health, owner and location pages follows Charges and Costs access.

From the November 2026 bug review:
- Full access to Health alone could create, change or delete billable
  charges by typing a cost on a health record (single and bulk).
- Farrier and vet costs showed to users who may not see charges.
- The owner data download held invoices, payments and charges for users
  who may not see them.
- The location feed tab showed feed costs to users who may not see costs.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import ExtraCharge, FeedOut
from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_user_with_access
from health.models import FarrierVisit, VetVisit


class MoneyVisibilityBase(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.owner = Owner.objects.create(name='Jo Bloggs', email='jo@example.com')
        self.location = Location.objects.create(site='Colgate', name='Top Field')
        rate = RateType.objects.create(name='Grass', daily_rate=Decimal('5.00'))
        self.horse = Horse.objects.create(name='Ghost')
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location, rate_type=rate,
            start_date=self.today - timedelta(days=30),
        )

    def _login(self, **levels):
        self.client.force_login(make_user_with_access(**levels))


class HealthCostNeedsChargesTests(MoneyVisibilityBase):

    def _add_vet_visit(self, cost):
        return self.client.post(reverse('vet_visit_create'), {
            'horse': self.horse.pk, 'date': self.today.isoformat(),
            'reason': 'Lame', 'cost': cost,
        })

    def test_without_charges_access_a_cost_creates_no_charge(self):
        self._login(health='full', charges='view')
        form = self.client.get(reverse('vet_visit_create'))
        self.assertNotIn('cost', form.context['form'].fields)

        self._add_vet_visit('999.00')

        visit = VetVisit.objects.get()
        self.assertEqual(visit.cost, Decimal('0.00'))
        self.assertFalse(ExtraCharge.objects.exists())

    def test_without_charges_access_an_edit_keeps_the_cost_and_charge(self):
        visit = VetVisit.objects.create(
            horse=self.horse, date=self.today, reason='Lame', cost=Decimal('80.00'),
        )
        from health.views import sync_record_charge
        sync_record_charge(visit)
        self._login(health='full')

        self.client.post(reverse('vet_visit_update', args=[visit.pk]), {
            'horse': self.horse.pk, 'date': self.today.isoformat(),
            'reason': 'Still lame', 'cost': '0',
        })

        visit.refresh_from_db()
        self.assertEqual(visit.reason, 'Still lame')
        self.assertEqual(visit.cost, Decimal('80.00'))
        self.assertEqual(ExtraCharge.objects.get().amount, Decimal('80.00'))

    def test_with_full_charges_access_a_cost_bills_the_owner(self):
        self._login(health='full', charges='full')
        self._add_vet_visit('120.00')
        charge = ExtraCharge.objects.get()
        self.assertEqual((charge.amount, charge.owner), (Decimal('120.00'), self.owner))

    def test_bulk_records_follow_the_same_rule(self):
        self._login(health='full')
        form = self.client.get(
            reverse('bulk_health_form'),
            {'action_type': 'vet_visit', 'horse_ids': [self.horse.pk]},
        )
        self.assertNotIn('cost', form.context['form'].fields)

        self.client.post(reverse('bulk_health_apply'), {
            'action_type': 'vet_visit', 'horse_ids': [self.horse.pk],
            'date': self.today.isoformat(), 'reason': 'Teeth', 'cost': '444.00',
        })

        self.assertEqual(VetVisit.objects.get().cost, Decimal('0.00'))
        self.assertFalse(ExtraCharge.objects.exists())

    def test_service_prices_are_hidden_with_the_cost(self):
        from billing.models import ServiceItem
        ServiceItem.objects.create(
            name='Trim', category=ServiceItem.Category.FARRIER, price=Decimal('37.50'),
        )
        self._login(health='full')
        response = self.client.get(reverse('farrier_create'))
        self.assertContains(response, 'Trim')
        self.assertNotContains(response, '37.50')


class HealthListCostTests(MoneyVisibilityBase):

    def setUp(self):
        super().setUp()
        FarrierVisit.objects.create(horse=self.horse, date=self.today, cost=Decimal('45.67'))
        VetVisit.objects.create(
            horse=self.horse, date=self.today, reason='Check', cost=Decimal('95.73'),
        )
        self.pages = [
            reverse('farrier_list'),
            reverse('vet_visit_list'),
            reverse('health_dashboard') + '?type=vet_visits',
        ]

    def test_costs_are_hidden_without_charges_access(self):
        self._login(health='view')
        for page in self.pages:
            response = self.client.get(page)
            self.assertEqual(response.status_code, 200, page)
            self.assertNotContains(response, '45.67')
            self.assertNotContains(response, '95.73')

    def test_costs_show_with_charges_access(self):
        self._login(health='view', charges='view')
        self.assertContains(self.client.get(self.pages[0]), '45.67')
        self.assertContains(self.client.get(self.pages[1]), '95.73')


class OwnerDataExportTests(MoneyVisibilityBase):

    def test_no_download_without_invoices_and_charges_access(self):
        self._login(owners='full', invoices='view')
        page = self.client.get(reverse('owner_detail', args=[self.owner.pk]))
        self.assertNotContains(page, reverse('owner_data_export', args=[self.owner.pk]))
        response = self.client.get(reverse('owner_data_export', args=[self.owner.pk]))
        self.assertRedirects(response, reverse('owner_detail', args=[self.owner.pk]),
                             fetch_redirect_response=False)

    def test_download_with_invoices_and_charges_access(self):
        self._login(owners='full', invoices='view', charges='view')
        response = self.client.get(reverse('owner_data_export', args=[self.owner.pk]))
        self.assertEqual(response['Content-Type'], 'application/zip')


class LocationFeedCostTests(MoneyVisibilityBase):

    def setUp(self):
        super().setUp()
        FeedOut.objects.create(
            location=self.location, date=self.today, feed_type='hay',
            total_cost=Decimal('66.61'),
        )
        self.url = reverse('location_detail', args=[self.location.pk]) + '?tab=feed'

    def test_feed_cost_is_hidden_without_costs_access(self):
        self._login(locations='view')
        self.assertNotContains(self.client.get(self.url), '66.61')

    def test_feed_cost_shows_with_costs_access(self):
        self._login(locations='view', costs='view')
        self.assertContains(self.client.get(self.url), '66.61')
