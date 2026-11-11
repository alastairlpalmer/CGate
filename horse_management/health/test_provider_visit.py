"""Farrier visit page, step 1: the data it stands on.

docs/FARRIER_PAGE_PLAN.md, sections 7 and 8. Covers the provider's login
link, the provider's own price list (kept out of the staff forms), the
visit that groups one day's work, and the Farrier role.
"""

import importlib
from datetime import timedelta
from decimal import Decimal

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.forms import ServicePickerField
from billing.models import ServiceItem, ServiceProvider
from core.features import FEATURES_BY_KEY, LEVEL_FULL, LEVEL_HIDDEN
from core.models import Horse, Role
from core.permissions import has_feature_access
from core.roles_testutils import assign_role, make_admin
from health.models import FarrierVisit, ProviderVisit

User = get_user_model()

seed_migration = importlib.import_module('core.migrations.0053_seed_farrier_role')


def make_farrier(name='Sam Shoe', username='farrier'):
    user = User.objects.create_user(username=username, password='x')
    provider = ServiceProvider.objects.create(
        name=name, provider_type=ServiceProvider.ProviderType.FARRIER, user=user,
    )
    return user, provider


class ProviderLoginTests(TestCase):
    def test_for_user_finds_the_linked_provider(self):
        user, provider = make_farrier()
        self.assertEqual(ServiceProvider.for_user(user), provider)

    def test_for_user_is_none_for_other_users(self):
        make_farrier()
        stranger = User.objects.create_user(username='stranger', password='x')
        self.assertIsNone(ServiceProvider.for_user(stranger))
        self.assertIsNone(ServiceProvider.for_user(AnonymousUser()))
        self.assertIsNone(ServiceProvider.for_user(None))

    def test_for_user_ignores_an_inactive_provider(self):
        # Ending a farrier's access: mark the provider inactive.
        user, provider = make_farrier()
        provider.is_active = False
        provider.save()
        self.assertIsNone(ServiceProvider.for_user(user))

    def test_one_login_links_to_one_provider_only(self):
        user, _ = make_farrier()
        with self.assertRaises(IntegrityError), transaction.atomic():
            ServiceProvider.objects.create(name='Second', provider_type='farrier', user=user)

    def test_deleting_the_login_keeps_the_provider(self):
        user, provider = make_farrier()
        user.delete()
        provider.refresh_from_db()
        self.assertIsNone(provider.user)


class ProviderPriceListTests(TestCase):
    def setUp(self):
        _, self.provider = make_farrier()
        self.yard_item = ServiceItem.objects.create(
            name='Front shoes (yard)', category='farrier', price=Decimal('45.00'),
            farrier_work='front_shoes',
        )
        self.own_item = ServiceItem.objects.create(
            name='Full set', category='farrier', price=Decimal('95.00'),
            farrier_work='full_set', provider=self.provider,
        )

    def test_yard_directory_excludes_provider_prices(self):
        self.assertEqual(list(ServiceItem.objects.yard()), [self.yard_item])

    def test_provider_price_list_holds_only_their_items(self):
        _, other = make_farrier(name='Other', username='other-farrier')
        ServiceItem.objects.create(name='Trim', category='farrier', price=Decimal('30.00'), provider=other)
        self.assertEqual(list(ServiceItem.objects.for_provider(self.provider)), [self.own_item])

    def test_staff_picker_never_offers_provider_prices(self):
        field = ServicePickerField(category='farrier')
        offered = {value for value, _ in field.choices}
        self.assertIn(str(self.yard_item.pk), offered)
        self.assertNotIn(str(self.own_item.pk), offered)

    def test_settings_list_shows_yard_items_only(self):
        self.client.force_login(make_admin(username='settings-admin'))
        response = self.client.get(reverse('app_settings'))
        self.assertEqual(response.status_code, 200)
        names = [i.name for i in response.context['service_items']]
        self.assertIn('Front shoes (yard)', names)
        self.assertNotIn('Full set', names)

    def test_deleting_a_provider_removes_their_price_list(self):
        self.provider.delete()
        self.assertFalse(ServiceItem.objects.filter(pk=self.own_item.pk).exists())
        self.assertTrue(ServiceItem.objects.filter(pk=self.yard_item.pk).exists())


class ProviderVisitTests(TestCase):
    def setUp(self):
        self.user, self.provider = make_farrier()
        self.horse = Horse.objects.create(name='Bramble')

    def test_new_visit_is_open_today(self):
        visit = ProviderVisit.objects.create(provider=self.provider, created_by=self.user)
        self.assertTrue(visit.is_open)
        self.assertEqual(visit.date, timezone.localdate())
        self.assertIsNone(visit.duration)
        self.assertFalse(visit.is_paid)

    def test_only_one_open_visit_per_provider(self):
        ProviderVisit.objects.create(provider=self.provider)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProviderVisit.objects.create(provider=self.provider)

    def test_each_provider_can_have_its_own_open_visit(self):
        _, other = make_farrier(name='Other', username='other-farrier')
        ProviderVisit.objects.create(provider=self.provider)
        ProviderVisit.objects.create(provider=other)
        self.assertEqual(ProviderVisit.objects.filter(status='open').count(), 2)

    def test_finished_visits_do_not_block_a_new_one(self):
        first = ProviderVisit.objects.create(provider=self.provider)
        first.status = ProviderVisit.Status.FINISHED
        first.finished_at = timezone.now()
        first.save()
        ProviderVisit.objects.create(provider=self.provider)
        self.assertEqual(self.provider.visits.count(), 2)

    def test_finished_visit_needs_a_finish_time(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProviderVisit.objects.create(provider=self.provider, status=ProviderVisit.Status.FINISHED)

    def test_total_and_duration(self):
        visit = ProviderVisit.objects.create(provider=self.provider)
        other = Horse.objects.create(name='Juno')
        for horse, work, cost in ((self.horse, 'full_set', '95.00'), (other, 'trim', '35.00')):
            FarrierVisit.objects.create(
                horse=horse, date=visit.date, service_provider=self.provider,
                work_done=work, cost=Decimal(cost), provider_visit=visit,
            )
        self.assertEqual(visit.total, Decimal('130.00'))
        visit.status = ProviderVisit.Status.FINISHED
        visit.finished_at = visit.started_at + timedelta(minutes=3)
        visit.save()
        self.assertEqual(visit.duration, timedelta(minutes=3))

    def test_empty_visit_totals_zero(self):
        visit = ProviderVisit.objects.create(provider=self.provider)
        self.assertEqual(visit.total, Decimal('0.00'))

    def test_deleting_a_visit_keeps_the_farrier_records(self):
        visit = ProviderVisit.objects.create(provider=self.provider)
        record = FarrierVisit.objects.create(
            horse=self.horse, date=visit.date, service_provider=self.provider, provider_visit=visit,
        )
        visit.delete()
        record.refresh_from_db()
        self.assertIsNone(record.provider_visit)

    def test_a_provider_with_visits_cannot_be_deleted(self):
        ProviderVisit.objects.create(provider=self.provider)
        with self.assertRaises(ProtectedError):
            self.provider.delete()

    def test_staff_records_have_no_visit(self):
        record = FarrierVisit.objects.create(horse=self.horse, date=timezone.localdate())
        self.assertIsNone(record.provider_visit)


class FarrierRoleTests(TestCase):
    def test_feature_key_is_binary(self):
        feature = FEATURES_BY_KEY['farrier_portal']
        self.assertFalse(feature['supports_view'])

    def test_seed_creates_role_with_only_the_farrier_page(self):
        seed_migration.seed_farrier_role(django_apps, None)
        role = Role.objects.get(name='Farrier')
        resolved = role.resolved_access()
        self.assertEqual(resolved['farrier_portal'], LEVEL_FULL)
        others = {key: level for key, level in resolved.items() if key != 'farrier_portal'}
        self.assertEqual(set(others.values()), {LEVEL_HIDDEN})

    def test_farrier_user_can_open_the_farrier_page_and_nothing_else(self):
        seed_migration.seed_farrier_role(django_apps, None)
        user, _ = make_farrier()
        assign_role(user, Role.objects.get(name='Farrier'))
        self.assertTrue(has_feature_access(user, 'farrier_portal', LEVEL_FULL))
        for key in ('dashboard', 'horses', 'owners', 'health', 'invoices', 'charges', 'settings'):
            self.assertFalse(has_feature_access(user, key), key)

    def test_seed_keeps_an_existing_farrier_role(self):
        Role.objects.create(name='Farrier', access={'farrier_portal': 'full', 'horses': 'view'})
        seed_migration.seed_farrier_role(django_apps, None)
        role = Role.objects.get(name='Farrier')
        self.assertEqual(role.access.get('horses'), 'view')

    def test_seed_map_only_names_known_features(self):
        self.assertLessEqual(set(seed_migration.FARRIER_ACCESS), set(FEATURES_BY_KEY))

    def test_staff_roles_do_not_get_the_farrier_page(self):
        role = Role.objects.create(name='Yard staff', access={'horses': 'full', 'health': 'full'})
        self.assertEqual(role.resolved_access()['farrier_portal'], LEVEL_HIDDEN)

    def test_unseed_leaves_a_role_someone_holds(self):
        seed_migration.seed_farrier_role(django_apps, None)
        user, _ = make_farrier()
        assign_role(user, Role.objects.get(name='Farrier'))
        seed_migration.unseed_farrier_role(django_apps, None)
        self.assertTrue(Role.objects.filter(name='Farrier').exists())
