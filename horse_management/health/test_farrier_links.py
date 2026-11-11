"""The Farrier page: one place for all farrier work.

The sidebar and phone menu have a "Farrier" item. Its page has tabs:
Records (every farrier record; was the Health page's Farrier tab),
Visits from the farrier's phone, and the Farrier's list. Records and the
list need Health: view; the visits need Charges: view. A Farrier-role
login never sees any of it.
"""

import importlib

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from billing.models import ServiceProvider
from core.models import Role
from core.roles_testutils import assign_role, make_admin, make_user_with_access

User = get_user_model()
seed_migration = importlib.import_module('core.migrations.0053_seed_farrier_role')

HUB_URL = reverse('farrier_list')
VISITS_URL = reverse('provider_visit_list')
LIST_URL = reverse('farrier_job_list')
NAV_ITEM = 'data-nav="farrier"'


def make_farrier_login():
    seed_migration.seed_farrier_role(django_apps, None)
    user = User.objects.create_user(username='farrier', password='pw')
    assign_role(user, Role.objects.get(name='Farrier'))
    ServiceProvider.objects.create(name='Sam Shoe', provider_type='farrier', user=user)
    return user


def tabs(response):
    body = response.content.decode()
    return [t for t in ('records', 'visits', 'list') if f'data-farrier-tab="{t}"' in body]


class MenuItemTests(TestCase):
    def test_sidebar_and_phone_menu_say_farrier(self):
        self.client.force_login(make_admin('boss'))
        response = self.client.get(reverse('health_dashboard'))
        self.assertContains(response, NAV_ITEM, count=2)
        self.assertContains(response, '<span class="sidebar-label">Farrier</span>', html=False)
        self.assertNotContains(response, 'Farrier Visits</span>')

    def test_item_shows_with_health_view_or_charges_view(self):
        for levels in ({'health': 'view'}, {'charges': 'view'}):
            with self.subTest(**levels):
                self.client.force_login(make_user_with_access('u' + '_'.join(levels), **levels))
                self.assertContains(self.client.get(HUB_URL, follow=True), NAV_ITEM, count=2)

    def test_no_item_without_health_or_charges(self):
        self.client.force_login(make_user_with_access('feeder', feed='full', dashboard='full'))
        self.assertNotContains(self.client.get('/'), NAV_ITEM)

    def test_farrier_is_active_on_every_tab_and_health_is_not(self):
        self.client.force_login(make_admin('boss'))
        for url in (HUB_URL, VISITS_URL, LIST_URL):
            with self.subTest(url=url):
                body = self.client.get(url).content.decode()
                item = body[body.index(f'href="{HUB_URL}" {NAV_ITEM}'):][:250]
                self.assertIn('nav-link-active', item)
                health = body[body.index(f'href="{reverse("health_dashboard")}"'):][:300]
                self.assertNotIn('nav-link-active', health)


class FarrierPageTests(TestCase):
    def test_admin_sees_all_three_tabs_and_the_records(self):
        self.client.force_login(make_admin('boss'))
        response = self.client.get(HUB_URL)
        self.assertEqual(tabs(response), ['records', 'visits', 'list'])
        self.assertTemplateUsed(response, 'health/partials/farrier_content.html')
        self.assertContains(response, 'Add a record')
        self.assertContains(response, 'Print QR code')

    def test_the_tabs_are_on_every_farrier_page(self):
        self.client.force_login(make_admin('boss'))
        for url in (VISITS_URL, LIST_URL):
            with self.subTest(url=url):
                self.assertEqual(tabs(self.client.get(url)), ['records', 'visits', 'list'])

    def test_health_view_without_charges_has_no_visits_tab(self):
        self.client.force_login(make_user_with_access('groom', health='view'))
        response = self.client.get(HUB_URL)
        self.assertEqual(tabs(response), ['records', 'list'])
        self.assertNotContains(response, 'Add a record')
        self.assertNotContains(response, 'Print QR code')

    def test_charges_only_lands_on_the_visits(self):
        self.client.force_login(make_user_with_access('bookkeeper', charges='view'))
        response = self.client.get(HUB_URL)
        self.assertRedirects(response, VISITS_URL, fetch_redirect_response=False)
        self.assertEqual(tabs(self.client.get(VISITS_URL)), ['visits'])

    def test_no_access_is_denied(self):
        self.client.force_login(make_user_with_access('feeder', feed='full', dashboard='full'))
        self.assertNotEqual(self.client.get(HUB_URL).status_code, 200)

    def test_the_health_page_has_no_farrier_tab_and_old_links_redirect(self):
        self.client.force_login(make_admin('boss'))
        response = self.client.get(reverse('health_dashboard'))
        self.assertNotContains(response, '?type=farrier')
        old = self.client.get(reverse('health_dashboard'), {'type': 'farrier', 'status': 'overdue'})
        self.assertRedirects(old, f'{HUB_URL}?status=overdue', fetch_redirect_response=False)

    def test_filters_and_paging_stay_on_the_farrier_page(self):
        self.client.force_login(make_admin('boss'))
        response = self.client.get(HUB_URL, {'status': 'due'}, HTTP_HX_REQUEST='true', HTTP_HX_TARGET='health-table-area')
        self.assertTemplateUsed(response, 'health/partials/farrier_content.html')
        self.assertTemplateNotUsed(response, 'health/farrier_hub.html')
        self.assertContains(response, f'hx-get="{HUB_URL}"')
        self.assertNotContains(response, 'type=farrier')

    def test_saving_a_record_returns_to_the_farrier_page(self):
        from core.models import Horse
        self.client.force_login(make_admin('boss'))
        horse = Horse.objects.create(name='Bramble')
        response = self.client.post(reverse('farrier_create'), {
            'horse': horse.pk, 'date': '2026-11-01', 'work_done': 'trim', 'cost': '0',
        })
        self.assertRedirects(response, HUB_URL, fetch_redirect_response=False)


class FarrierLoginNeverSeesItTests(TestCase):
    def test_farrier_login_is_kept_off_the_farrier_page_for_staff(self):
        self.client.force_login(make_farrier_login())
        for url in (HUB_URL, VISITS_URL, LIST_URL):
            with self.subTest(url=url):
                self.assertRedirects(self.client.get(url), '/farrier/', fetch_redirect_response=False)

    def test_the_farrier_page_on_the_phone_has_no_link_to_it(self):
        self.client.force_login(make_farrier_login())
        response = self.client.get('/farrier/')
        self.assertNotContains(response, f'href="{HUB_URL}"')
        self.assertNotContains(response, NAV_ITEM)


class VisitsPageTests(TestCase):
    def test_print_qr_code_needs_settings_full(self):
        self.client.force_login(make_user_with_access('office', health='full', charges='view', settings='view'))
        self.assertNotContains(self.client.get(VISITS_URL), 'Print QR code')
        self.client.force_login(make_admin('boss'))
        self.assertContains(self.client.get(VISITS_URL), reverse('farrier_qr'))
