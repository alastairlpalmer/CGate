"""Staff can find the farrier visits page (/health/farrier-visits/).

Reported on the live site: nothing linked to it. The button was on
health/farrier_list.html, an older page no menu opens; the Health page's
Farrier tab renders health/partials/farrier_content.html instead. The
links are now on that tab, in the sidebar and in the phone menu. The rule
for each is charges: view. A Farrier-role login never sees them.
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

VISITS_URL = reverse('provider_visit_list')
FARRIER_TAB = f"{reverse('health_dashboard')}?type=farrier"
TAB_BUTTON = 'data-farrier-visits-link'
NAV_ITEM = 'data-nav="farrier-visits"'


def make_farrier_login():
    seed_migration.seed_farrier_role(django_apps, None)
    user = User.objects.create_user(username='farrier', password='pw')
    assign_role(user, Role.objects.get(name='Farrier'))
    ServiceProvider.objects.create(name='Sam Shoe', provider_type='farrier', user=user)
    return user


class FarrierTabButtonTests(TestCase):
    def test_the_farrier_tab_renders_the_button(self):
        self.client.force_login(make_user_with_access('office', health='full', charges='view'))
        response = self.client.get(FARRIER_TAB)
        self.assertContains(response, TAB_BUTTON)
        self.assertContains(response, '>Farrier Visits</a>')
        self.assertContains(response, f'href="{VISITS_URL}"')

    def test_the_tab_loaded_by_htmx_has_the_button_too(self):
        # The tabs swap #health-table-area with only the partial.
        self.client.force_login(make_user_with_access('office', health='full', charges='view'))
        response = self.client.get(FARRIER_TAB, HTTP_HX_REQUEST='true', HTTP_HX_TARGET='health-table-area')
        self.assertTemplateUsed(response, 'health/partials/farrier_content.html')
        self.assertContains(response, TAB_BUTTON)

    def test_no_button_without_charges_view(self):
        self.client.force_login(make_user_with_access('groom', health='full'))
        response = self.client.get(FARRIER_TAB)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, TAB_BUTTON)


class MenuItemTests(TestCase):
    def test_sidebar_and_phone_menu_show_the_item(self):
        self.client.force_login(make_user_with_access('office', health='full', charges='view'))
        response = self.client.get(reverse('health_dashboard'))
        # Once in the desktop sidebar, once in the phone menu.
        self.assertContains(response, NAV_ITEM, count=2)

    def test_item_shows_with_charges_view_alone(self):
        self.client.force_login(make_user_with_access('bookkeeper', charges='view'))
        response = self.client.get(VISITS_URL)
        self.assertContains(response, NAV_ITEM, count=2)

    def test_no_item_without_charges_view(self):
        self.client.force_login(make_user_with_access('groom', health='full'))
        response = self.client.get(reverse('health_dashboard'))
        self.assertNotContains(response, NAV_ITEM)

    def test_the_item_is_active_on_its_page_and_health_is_not(self):
        self.client.force_login(make_admin('boss'))
        body = self.client.get(VISITS_URL).content.decode()
        side = body[body.index(f'href="{VISITS_URL}" {NAV_ITEM}'):][:200]
        self.assertIn('nav-link-active', side)
        health = body[body.index(f'href="{reverse("health_dashboard")}"'):][:300]
        self.assertNotIn('nav-link-active', health)


class FarrierLoginNeverSeesTheLinksTests(TestCase):
    def test_farrier_login_is_kept_off_the_staff_pages(self):
        self.client.force_login(make_farrier_login())
        for url in (FARRIER_TAB, VISITS_URL):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertRedirects(response, '/farrier/', fetch_redirect_response=False)

    def test_farrier_page_has_no_link_to_the_yard_side(self):
        self.client.force_login(make_farrier_login())
        response = self.client.get('/farrier/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, VISITS_URL)
        self.assertNotContains(response, NAV_ITEM)
        self.assertNotContains(response, TAB_BUTTON)


class VisitsPageLinksTests(TestCase):
    def test_admin_sees_print_qr_code_and_a_link_back_to_health(self):
        self.client.force_login(make_admin('boss'))
        response = self.client.get(VISITS_URL)
        self.assertContains(response, reverse('farrier_qr'))
        self.assertContains(response, 'Print QR code')
        self.assertContains(response, f'href="{FARRIER_TAB}"')

    def test_no_print_qr_code_without_settings_full(self):
        self.client.force_login(make_user_with_access('office', health='full', charges='view', settings='view'))
        response = self.client.get(VISITS_URL)
        self.assertNotContains(response, 'Print QR code')
        self.assertContains(response, 'Back to Health')
