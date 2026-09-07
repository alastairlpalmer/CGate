"""Services directory: set prices once in Settings, pick the service on a
record form and the price (and the record's defaults) fill in by itself.

Covers the starter list, the picker on single and bulk forms, the
"override" and "Other" paths, the bulk Add Charge action, and the egg
count record now being billable.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import ExtraCharge, ServiceItem
from billing.service_directory import DEFAULT_SERVICE_ITEMS, seed_default_services
from core.models import Horse, Location, Owner, OwnershipShare, Placement, RateType
from core.roles_testutils import make_admin, make_user_with_access
from health.models import FarrierVisit, VaccinationType, WormEggCount, WormingTreatment


class ServiceDirectoryBase(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.owner = Owner.objects.create(name='Jo Bloggs')
        self.location = Location.objects.create(name='Top Field', site='Main')
        self.rate = RateType.objects.create(name='Full', daily_rate=10)
        self.horses = [Horse.objects.create(name=f'SVC{i}') for i in range(2)]
        for horse in self.horses:
            OwnershipShare.objects.create(
                horse=horse, owner=self.owner,
                share_percentage=100, is_primary_contact=True,
            )
            Placement.objects.create(
                horse=horse, owner=self.owner, location=self.location,
                rate_type=self.rate, start_date=self.today - timedelta(days=30),
            )
        self.horse = self.horses[0]
        self.flu = VaccinationType.objects.create(name='Flu', interval_months=6)
        self.equimax = ServiceItem.objects.create(
            name='Wormer - Equimax', category='worming', price=Decimal('17.00'),
        )
        self.front_shoes = ServiceItem.objects.create(
            name='Front shoes', category='farrier', price=Decimal('45.00'),
            farrier_work='front_shoes',
        )
        self.transport = ServiceItem.objects.create(
            name='Transport', category='transport', price=Decimal('100.00'),
        )
        self.worm_test = ServiceItem.objects.create(
            name='Worm test', category='egg_count', price=Decimal('12.00'),
        )
        self.client.force_login(make_admin(username='svc-admin'))

    def _ids(self):
        return [str(h.pk) for h in self.horses]


class SeedTests(TestCase):
    def test_starter_list_seeds_once_and_links_the_flu_type(self):
        flu = VaccinationType.objects.create(name='Equine Flu', interval_months=6)
        self.assertEqual(seed_default_services(ServiceItem, VaccinationType), len(DEFAULT_SERVICE_ITEMS))
        self.assertEqual(ServiceItem.objects.count(), len(DEFAULT_SERVICE_ITEMS))
        self.assertEqual(ServiceItem.objects.get(name='Vaccination - Flu').vaccination_type, flu)
        self.assertEqual(ServiceItem.objects.get(name='Shoes all').farrier_work, 'full_set')
        self.assertEqual(ServiceItem.objects.get(name='VET Wormers - Panacur').price, Decimal('21.50'))
        # A directory with rows is the user's — never re-seeded on top.
        self.assertEqual(seed_default_services(ServiceItem, VaccinationType), 0)
        self.assertEqual(ServiceItem.objects.count(), len(DEFAULT_SERVICE_ITEMS))

    def test_charge_type_follows_category(self):
        item = ServiceItem(name='x', category='egg_count', price=1)
        self.assertEqual(item.charge_type, 'vet')
        self.assertEqual(ServiceItem(name='x', category='transport', price=1).charge_type, 'transport')
        self.assertIsNone(item.fill_value('farrier_work'))
        self.assertEqual(item.fill_value('name'), 'x')


class PickerRenderingTests(ServiceDirectoryBase):
    def test_bulk_form_offers_matching_services_with_price_and_other(self):
        response = self.client.get(
            reverse('bulk_health_form'),
            {'action_type': 'worming', 'horse_ids': self._ids()},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="service_item"')
        self.assertContains(response, 'data-service-picker')
        self.assertContains(response, f'<option value="{self.equimax.pk}" data-price="17.00"')
        self.assertContains(response, 'Wormer - Equimax — £17.00')
        self.assertContains(response, '<option value="other">Other (enter details manually)</option>', html=True)
        # A farrier item does not appear on the worming form
        self.assertNotContains(response, 'Front shoes')
        # The cost input is the one the picker fills
        self.assertContains(response, 'data-service-cost')

    def test_inactive_services_are_not_offered(self):
        self.equimax.is_active = False
        self.equimax.save()
        response = self.client.get(
            reverse('bulk_health_form'),
            {'action_type': 'worming', 'horse_ids': self._ids()},
        )
        self.assertNotContains(response, 'Wormer - Equimax')

    def test_charge_form_offers_every_category_grouped(self):
        response = self.client.get(reverse('charge_create'))
        self.assertContains(response, '<optgroup label="Wormer">')
        self.assertContains(response, '<optgroup label="Transport">')
        self.assertContains(response, f'<option value="{self.transport.pk}" data-price="100.00"')

    def test_full_page_record_forms_show_the_picker(self):
        for name in ('vaccination_create', 'farrier_create', 'worming_create',
                     'egg_count_create', 'vet_visit_create'):
            response = self.client.get(reverse(name))
            self.assertContains(response, 'name="service_item"', msg_prefix=name)

    def test_bulk_bar_offers_add_charge_to_charge_editors_only(self):
        response = self.client.get(reverse('horse_list'))
        self.assertContains(response, 'value="charge"')
        self.client.force_login(make_user_with_access(
            username='no-charges', horses='view', health='full', charges='view',
        ))
        response = self.client.get(reverse('horse_list'))
        self.assertNotContains(response, 'value="charge"')


class ServiceFillTests(ServiceDirectoryBase):
    def _bulk_worming(self, **overrides):
        data = {
            'action_type': 'worming',
            'horse_ids': self._ids(),
            'service_item': str(self.equimax.pk),
            'date': self.today.isoformat(),
            'product_name': '',
            'active_ingredient': '',
            'dose': '',
            'administered_by': '',
            'cost': '',
            'notes': '',
        }
        data.update(overrides)
        return self.client.post(reverse('bulk_health_apply'), data)

    def test_picking_a_service_fills_price_and_product_for_every_horse(self):
        response = self._bulk_worming()
        self.assertEqual(response.status_code, 204)
        treatments = WormingTreatment.objects.filter(horse__in=self.horses)
        self.assertEqual(treatments.count(), 2)
        for t in treatments:
            self.assertEqual(t.product_name, 'Wormer - Equimax')
            self.assertEqual(t.cost, Decimal('17.00'))
            self.assertEqual(t.extra_charge.amount, Decimal('17.00'))
            self.assertEqual(t.extra_charge.owner, self.owner)

    def test_override_keeps_the_typed_price(self):
        response = self._bulk_worming(cost='15.50')
        self.assertEqual(response.status_code, 204)
        for t in WormingTreatment.objects.filter(horse__in=self.horses):
            self.assertEqual(t.cost, Decimal('15.50'))
            self.assertEqual(t.product_name, 'Wormer - Equimax')

    def test_typed_details_win_over_the_service_defaults(self):
        self._bulk_worming(product_name='Equimax (double dose)')
        t = WormingTreatment.objects.get(horse=self.horse)
        self.assertEqual(t.product_name, 'Equimax (double dose)')

    def test_other_is_free_form(self):
        response = self._bulk_worming(service_item='other', product_name='Own wormer', cost='3.25')
        self.assertEqual(response.status_code, 204)
        t = WormingTreatment.objects.get(horse=self.horse)
        self.assertEqual(t.product_name, 'Own wormer')
        self.assertEqual(t.cost, Decimal('3.25'))

    def test_other_without_details_is_still_validated(self):
        response = self._bulk_worming(service_item='other')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'field-invalid')
        self.assertFalse(WormingTreatment.objects.exists())

    def test_unknown_service_is_rejected(self):
        response = self._bulk_worming(service_item='999999')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WormingTreatment.objects.exists())

    def test_single_farrier_form_takes_the_service_price(self):
        response = self.client.post(reverse('farrier_create'), {
            'service_item': str(self.front_shoes.pk),
            'horse': self.horse.pk,
            'date': self.today.isoformat(),
            'service_provider': '',
            'work_done': 'front_shoes',
            'next_due_date': '',
            'cost': '',
            'notes': '',
        })
        self.assertEqual(response.status_code, 302)
        visit = FarrierVisit.objects.get(horse=self.horse)
        self.assertEqual(visit.cost, Decimal('45.00'))
        self.assertEqual(visit.work_done, 'front_shoes')
        self.assertEqual(visit.extra_charge.charge_type, 'farrier')

    def test_charge_form_fills_description_type_and_amount(self):
        response = self.client.post(reverse('charge_create'), {
            'service_item': str(self.transport.pk),
            'horse': self.horse.pk,
            'owner': self.owner.pk,
            'service_provider': '',
            'charge_type': 'other',
            'date': self.today.isoformat(),
            'description': '',
            'amount': '',
            'split_by_ownership': 'on',
            'notes': '',
        })
        self.assertEqual(response.status_code, 302)
        charge = ExtraCharge.objects.get(horse=self.horse)
        self.assertEqual(charge.description, 'Transport')
        self.assertEqual(charge.amount, Decimal('100.00'))
        # A select always posts a value, so the typed type is respected
        self.assertEqual(charge.charge_type, 'other')


class BulkChargeTests(ServiceDirectoryBase):
    def _post(self, **overrides):
        data = {
            'action_type': 'charge',
            'horse_ids': self._ids(),
            'service_item': str(self.transport.pk),
            'date': self.today.isoformat(),
            'charge_type': 'other',
            'description': '',
            'amount': '',
            'service_provider': '',
            'notes': '',
        }
        data.update(overrides)
        return self.client.post(reverse('bulk_health_apply'), data)

    def test_form_opens_for_the_selection(self):
        response = self.client.get(
            reverse('bulk_health_form'),
            {'action_type': 'charge', 'horse_ids': self._ids()},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Charge details')
        self.assertContains(response, 'Transport — £100.00')
        self.assertContains(response, 'Record for 2 horses')

    def test_one_charge_per_horse_billed_to_its_owner(self):
        response = self._post()
        self.assertEqual(response.status_code, 204)
        charges = ExtraCharge.objects.filter(horse__in=self.horses)
        self.assertEqual(charges.count(), 2)
        for charge in charges:
            self.assertEqual(charge.owner, self.owner)
            self.assertEqual(charge.amount, Decimal('100.00'))
            self.assertEqual(charge.description, 'Transport')
            self.assertEqual(charge.charge_type, 'other')
            self.assertFalse(charge.invoiced)

    def test_horse_without_an_owner_is_reported_not_charged(self):
        stray = Horse.objects.create(name='STRAY')
        response = self._post(horse_ids=self._ids() + [str(stray.pk)])
        self.assertEqual(response.status_code, 204)
        self.assertFalse(ExtraCharge.objects.filter(horse=stray).exists())
        self.assertEqual(ExtraCharge.objects.count(), 2)

    def test_amount_required_without_a_service(self):
        response = self._post(service_item='other', description='Ad hoc')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Enter an amount, or choose a service')
        self.assertFalse(ExtraCharge.objects.exists())

    def test_needs_charges_full_access(self):
        self.client.force_login(make_user_with_access(
            username='health-only', horses='view', health='full', charges='view',
        ))
        response = self.client.get(
            reverse('bulk_health_form'),
            {'action_type': 'charge', 'horse_ids': self._ids()},
        )
        self.assertEqual(response.status_code, 403)
        response = self._post()
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ExtraCharge.objects.exists())


class EggCountBillingTests(ServiceDirectoryBase):
    def test_egg_count_with_a_cost_bills_the_owner(self):
        response = self.client.post(reverse('egg_count_create'), {
            'service_item': str(self.worm_test.pk),
            'horse': self.horse.pk,
            'date': self.today.isoformat(),
            'count': '350',
            'lab_name': '',
            'sample_type': 'fec',
            'cost': '',
            'notes': '',
        })
        self.assertEqual(response.status_code, 302)
        record = WormEggCount.objects.get(horse=self.horse)
        self.assertEqual(record.cost, Decimal('12.00'))
        self.assertEqual(record.extra_charge.amount, Decimal('12.00'))
        self.assertEqual(record.extra_charge.charge_type, 'vet')
        self.assertIn('Worm egg count', record.extra_charge.description)

    def test_bulk_egg_count_bills_each_horse(self):
        response = self.client.post(reverse('bulk_health_apply'), {
            'action_type': 'egg_count',
            'horse_ids': self._ids(),
            'service_item': str(self.worm_test.pk),
            'date': self.today.isoformat(),
            'count': '50',
            'lab_name': '',
            'sample_type': 'fec',
            'cost': '',
            'notes': '',
        })
        self.assertEqual(response.status_code, 204)
        self.assertEqual(ExtraCharge.objects.filter(horse__in=self.horses).count(), 2)

    def test_deleting_the_charge_zeroes_the_record_cost(self):
        record = WormEggCount.objects.create(
            horse=self.horse, date=self.today, count=10, cost=Decimal('12.00'),
        )
        from health.views import sync_record_charge
        self.assertEqual(sync_record_charge(record), 'created')
        response = self.client.post(reverse('charge_delete', args=[record.extra_charge.pk]))
        self.assertEqual(response.status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.cost, Decimal('0.00'))
        self.assertIsNone(record.extra_charge)

    def test_level_key(self):
        def level(count):
            return WormEggCount(horse=self.horse, date=self.today, count=count).level
        self.assertEqual(level(0), 'low')
        self.assertEqual(level(199), 'low')
        self.assertEqual(level(200), 'mild')
        self.assertEqual(level(499), 'mild')
        self.assertEqual(level(500), 'moderate')
        self.assertEqual(level(999), 'moderate')
        self.assertEqual(level(1000), 'high')
        self.assertEqual(level(4000), 'high')
        self.assertTrue(WormEggCount(horse=self.horse, date=self.today, count=200).is_high)
        self.assertFalse(WormEggCount(horse=self.horse, date=self.today, count=199).is_high)


class SettingsTests(ServiceDirectoryBase):
    def test_settings_lists_services_by_category(self):
        response = self.client.get(reverse('app_settings'))
        self.assertContains(response, 'Services &amp; Prices')
        self.assertContains(response, 'Wormer - Equimax')
        self.assertContains(response, '£17.00 per horse')
        self.assertContains(response, reverse('service_item_update', args=[self.equimax.pk]))
        self.assertContains(response, reverse('service_item_create'))

    def test_add_and_edit_a_service(self):
        response = self.client.post(reverse('service_item_create'), {
            'name': 'Trim', 'category': 'farrier', 'price': '45.00',
            'farrier_work': 'trim', 'vaccination_type': '', 'sort_order': '0', 'is_active': 'on',
        })
        self.assertEqual(response.status_code, 302)
        trim = ServiceItem.objects.get(name='Trim')
        self.assertEqual(trim.farrier_work, 'trim')

        response = self.client.post(reverse('service_item_update', args=[trim.pk]), {
            'name': 'Trim', 'category': 'farrier', 'price': '48.00',
            'farrier_work': 'trim', 'vaccination_type': '', 'sort_order': '0', 'is_active': 'on',
        })
        self.assertEqual(response.status_code, 302)
        trim.refresh_from_db()
        self.assertEqual(trim.price, Decimal('48.00'))

    def test_category_specific_fields_are_cleared_for_other_categories(self):
        self.client.post(reverse('service_item_create'), {
            'name': 'Flu jab', 'category': 'vaccination', 'price': '50.00',
            'farrier_work': 'trim', 'vaccination_type': str(self.flu.pk),
            'sort_order': '0', 'is_active': 'on',
        })
        item = ServiceItem.objects.get(name='Flu jab')
        self.assertEqual(item.vaccination_type, self.flu)
        self.assertEqual(item.farrier_work, '')

    def test_settings_access_required(self):
        # A plain GET is redirected away with a message; a POST is refused.
        self.client.force_login(make_user_with_access(username='no-settings', horses='view'))
        self.assertEqual(self.client.get(reverse('service_item_create')).status_code, 302)
        response = self.client.post(reverse('service_item_update', args=[self.equimax.pk]), {
            'name': 'Hacked', 'category': 'worming', 'price': '1.00',
            'farrier_work': '', 'vaccination_type': '', 'sort_order': '0', 'is_active': 'on',
        })
        self.assertEqual(response.status_code, 403)
        self.equimax.refresh_from_db()
        self.assertEqual(self.equimax.name, 'Wormer - Equimax')


class VaccinationServiceTests(ServiceDirectoryBase):
    def test_service_supplies_the_vaccination_type_and_price(self):
        item = ServiceItem.objects.create(
            name='Vaccination - Flu', category='vaccination', price=Decimal('50.00'),
            vaccination_type=self.flu,
        )
        response = self.client.post(reverse('bulk_health_apply'), {
            'action_type': 'vaccination',
            'horse_ids': self._ids(),
            'service_item': str(item.pk),
            'vaccination_type': '',
            'date_given': self.today.isoformat(),
            'next_due_date': '',
            'vet': '',
            'batch_number': '',
            'cost': '',
            'notes': '',
        })
        self.assertEqual(response.status_code, 204)
        vax = self.horse.vaccinations.get()
        self.assertEqual(vax.vaccination_type, self.flu)
        self.assertEqual(vax.cost, Decimal('50.00'))
        self.assertEqual(vax.extra_charge.charge_type, 'vaccination')
