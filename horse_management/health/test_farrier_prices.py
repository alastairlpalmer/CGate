"""Farrier page, step 5: My prices and same-day changes.

docs/FARRIER_PAGE_PLAN.md, section 5 (screens 3 and 4). The farrier keeps
their own price list, and can change or remove a line of a finished visit
on the same day, until the yard checks it, and never once it is invoiced.
"""

import importlib
from datetime import timedelta
from decimal import Decimal

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import ExtraCharge, ServiceItem, ServiceProvider
from core.models import Horse, Location, Owner, OwnershipShare, Placement, RateType, Role
from core.roles_testutils import assign_role
from health.models import FarrierVisit, ProviderVisit
from invoicing.services import InvoiceService

User = get_user_model()
seed_migration = importlib.import_module('core.migrations.0053_seed_farrier_role')


def make_farrier(username='farrier', name='Sam Shoe'):
    seed_migration.seed_farrier_role(django_apps, None)
    user = User.objects.create_user(username=username, password='pw')
    assign_role(user, Role.objects.get(name='Farrier'))
    provider = ServiceProvider.objects.create(name=name, provider_type='farrier', user=user)
    return user, provider


class PriceListTests(TestCase):
    def setUp(self):
        self.user, self.provider = make_farrier()
        self.client.force_login(self.user)

    def post_price(self, url=None, **data):
        return self.client.post(url or reverse('farrier:price_add'), data)

    def test_add_a_price(self):
        response = self.post_price(name='Full set', work_done='full_set', price='95')
        self.assertRedirects(response, reverse('farrier:price_list'), fetch_redirect_response=False)
        item = ServiceItem.objects.get(name='Full set')
        self.assertEqual(
            (item.provider, item.category, item.farrier_work, item.price, item.is_active),
            (self.provider, 'farrier', 'full_set', Decimal('95.00'), True),
        )

    def test_new_prices_go_to_the_end_of_the_list(self):
        self.post_price(name='Trim', work_done='trim', price='35')
        self.post_price(name='Full set', work_done='full_set', price='95')
        names = [i.name for i in ServiceItem.objects.for_provider(self.provider).order_by('sort_order')]
        self.assertEqual(names, ['Trim', 'Full set'])

    def test_other_work_has_no_work_type(self):
        self.post_price(name='Rasp and balance', work_done='', price='25.50')
        self.assertEqual(ServiceItem.objects.get(name='Rasp and balance').farrier_work, '')

    def test_bad_input_is_refused_and_kept_on_the_form(self):
        cases = [
            ({'name': '', 'work_done': 'trim', 'price': '35'}, 'Enter a name'),
            ({'name': 'Trim', 'work_done': 'trim', 'price': 'cheap'}, 'Enter a price'),
            ({'name': 'Trim', 'work_done': 'trim', 'price': '-1'}, 'between £0'),
            ({'name': 'Trim', 'work_done': 'nonsense', 'price': '35'}, 'Choose a work type'),
        ]
        for data, text in cases:
            with self.subTest(text=text):
                response = self.post_price(**data)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, text)
        self.assertFalse(ServiceItem.objects.exists())

    def test_names_are_unique_per_farrier(self):
        self.post_price(name='Trim', work_done='trim', price='35')
        response = self.post_price(name='trim', work_done='trim', price='40')
        self.assertContains(response, 'already have a price with this name')
        _, rival = make_farrier(username='rival', name='Rival Forge')
        ServiceItem.objects.create(name='Shoes', category='farrier', price=Decimal('80'), provider=rival)
        self.post_price(name='Shoes', work_done='front_shoes', price='75')
        self.assertTrue(ServiceItem.objects.filter(name='Shoes', provider=self.provider).exists())

    def test_edit_own_price(self):
        self.post_price(name='Trim', work_done='trim', price='35')
        item = ServiceItem.objects.get(name='Trim')
        response = self.client.get(reverse('farrier:price_edit', args=[item.pk]))
        self.assertContains(response, 'value="35.00"')
        self.post_price(reverse('farrier:price_edit', args=[item.pk]), name='Trim', work_done='trim', price='38')
        item.refresh_from_db()
        self.assertEqual(item.price, Decimal('38.00'))
        self.assertEqual(ServiceItem.objects.count(), 1)

    def test_another_farriers_price_or_the_yards_is_not_found(self):
        _, rival = make_farrier(username='rival', name='Rival Forge')
        theirs = ServiceItem.objects.create(name='Trim', category='farrier', price=Decimal('30'), provider=rival)
        yard = ServiceItem.objects.create(name='Yard trim', category='farrier', price=Decimal('30'))
        for item in (theirs, yard):
            with self.subTest(item=item.name):
                self.assertEqual(self.client.get(reverse('farrier:price_edit', args=[item.pk])).status_code, 404)
                self.assertEqual(self.client.post(reverse('farrier:price_toggle', args=[item.pk])).status_code, 404)
        theirs.refresh_from_db()
        self.assertTrue(theirs.is_active)

    def test_hide_and_show_a_price(self):
        self.post_price(name='Trim', work_done='trim', price='35')
        item = ServiceItem.objects.get(name='Trim')
        self.client.post(reverse('farrier:price_toggle', args=[item.pk]))
        item.refresh_from_db()
        self.assertFalse(item.is_active)
        page = self.client.get(reverse('farrier:price_list'))
        self.assertContains(page, 'Trim (hidden)')
        self.client.post(reverse('farrier:price_toggle', args=[item.pk]))
        item.refresh_from_db()
        self.assertTrue(item.is_active)

    def test_toggle_needs_post(self):
        self.post_price(name='Trim', work_done='trim', price='35')
        item = ServiceItem.objects.get(name='Trim')
        self.assertEqual(self.client.get(reverse('farrier:price_toggle', args=[item.pk])).status_code, 405)

    def test_new_prices_never_reach_the_staff_forms(self):
        self.post_price(name='Secret set', work_done='full_set', price='95')
        self.assertFalse(ServiceItem.objects.yard().filter(name='Secret set').exists())


class SameDayChangeTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.user, self.provider = make_farrier()
        self.full_set = ServiceItem.objects.create(
            name='Full set', category='farrier', price=Decimal('95.00'), farrier_work='full_set', provider=self.provider,
        )
        self.trim = ServiceItem.objects.create(
            name='Trim', category='farrier', price=Decimal('35.00'), farrier_work='trim', provider=self.provider,
        )
        self.owner = Owner.objects.create(name='Jo Bloggs')
        self.horse = Horse.objects.create(name='Bramble')
        OwnershipShare.objects.create(horse=self.horse, owner=self.owner, share_percentage=100, is_primary_contact=True)
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=Location.objects.create(name='Top', site='Main'),
            rate_type=RateType.objects.create(name='Grass', daily_rate=5),
            start_date=self.today - timedelta(days=60),
        )
        self.client.force_login(self.user)
        self.client.post(reverse('farrier:horse_add', args=[self.horse.pk]), {'item': self.full_set.pk})
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('farrier:visit_finish'))
        self.visit = ProviderVisit.objects.get(provider=self.provider)
        self.line = self.visit.farrier_visits.get()

    def edit_url(self, line=None):
        return reverse('farrier:line_edit', args=[self.visit.pk, (line or self.line).pk])

    def test_change_a_line_updates_its_charge(self):
        response = self.client.post(self.edit_url(), {'item': self.trim.pk})
        self.assertRedirects(response, reverse('farrier:visit_detail', args=[self.visit.pk]),
                             fetch_redirect_response=False)
        self.line.refresh_from_db()
        self.assertEqual((self.line.work_done, self.line.cost), ('trim', Decimal('35.00')))
        charge = ExtraCharge.objects.get()
        self.assertEqual(charge.amount, Decimal('35.00'))
        self.assertEqual(charge.description, 'Farrier - Trim Only')

    def test_price_override_on_a_change(self):
        self.client.post(self.edit_url(), {'item': self.full_set.pk, 'price': '110'})
        self.assertEqual(ExtraCharge.objects.get().amount, Decimal('110.00'))

    def test_changing_to_free_removes_the_charge(self):
        self.client.post(self.edit_url(), {'item': self.full_set.pk, 'price': '0'})
        self.assertFalse(ExtraCharge.objects.exists())
        self.assertTrue(FarrierVisit.objects.filter(pk=self.line.pk).exists())

    def test_remove_a_line_and_its_charge(self):
        response = self.client.post(reverse('farrier:line_remove', args=[self.visit.pk, self.line.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(FarrierVisit.objects.filter(pk=self.line.pk).exists())
        self.assertFalse(ExtraCharge.objects.exists())

    def test_visit_page_offers_change_today(self):
        page = self.client.get(reverse('farrier:visit_detail', args=[self.visit.pk]))
        self.assertContains(page, self.edit_url())

    def test_no_change_after_the_yard_checks_the_visit(self):
        self.visit.checked_at = timezone.now()
        self.visit.save()
        page = self.client.get(reverse('farrier:visit_detail', args=[self.visit.pk]))
        self.assertNotContains(page, self.edit_url())
        response = self.client.get(self.edit_url())
        self.assertEqual(response.status_code, 302)
        self.client.post(reverse('farrier:line_remove', args=[self.visit.pk, self.line.pk]))
        self.assertTrue(FarrierVisit.objects.filter(pk=self.line.pk).exists())
        self.assertEqual(ExtraCharge.objects.get().amount, Decimal('95.00'))

    def test_no_change_on_another_day(self):
        ProviderVisit.objects.filter(pk=self.visit.pk).update(date=self.today - timedelta(days=1))
        response = self.client.post(self.edit_url(), {'item': self.trim.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ExtraCharge.objects.get().amount, Decimal('95.00'))

    def test_no_change_once_the_line_is_invoiced(self):
        start = self.today.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        InvoiceService.create_invoice(self.owner, start, end)
        response = self.client.post(self.edit_url(), {'item': self.trim.pk})
        self.assertContains(response, 'already on an invoice')
        self.assertEqual(ExtraCharge.objects.get().amount, Decimal('95.00'))
        self.client.post(reverse('farrier:line_remove', args=[self.visit.pk, self.line.pk]))
        self.assertTrue(FarrierVisit.objects.filter(pk=self.line.pk).exists())

    def test_another_farriers_line_is_not_found(self):
        rival_user, _ = make_farrier(username='rival', name='Rival Forge')
        self.client.force_login(rival_user)
        self.assertEqual(self.client.get(self.edit_url()).status_code, 404)
        self.assertEqual(
            self.client.post(reverse('farrier:line_remove', args=[self.visit.pk, self.line.pk])).status_code, 404,
        )

    def test_line_must_belong_to_the_visit_in_the_address(self):
        other_visit = ProviderVisit.objects.create(provider=self.provider)
        url = reverse('farrier:line_edit', args=[other_visit.pk, self.line.pk])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_staff_records_cannot_be_reached(self):
        staff_line = FarrierVisit.objects.create(horse=self.horse, date=self.today, work_done='trim')
        url = reverse('farrier:line_edit', args=[self.visit.pk, staff_line.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
