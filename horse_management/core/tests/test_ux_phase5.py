"""Shorter forms and a settings page in groups (docs/UX_REVIEW_PLAN.md,
phase 5)."""
from django import forms
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from core.roles_testutils import make_admin, make_user_with_access
from core.widgets import MoneyInput
from health.forms import FarrierVisitForm, VaccinationForm


class BlankOptionTests(SimpleTestCase):
    def test_django_dashes_read_as_choose(self):
        field = forms.ChoiceField(choices=[('', '---------'), ('a', 'A')])
        html = field.widget.render('x', '')
        self.assertIn('>Choose…</option>', html)
        self.assertNotIn('---------', html)

    def test_a_label_of_its_own_is_kept(self):
        field = forms.ChoiceField(choices=[('', 'Pick one'), ('a', 'A')])
        self.assertIn('>Pick one</option>', field.widget.render('x', ''))


class MoneyInputTests(SimpleTestCase):
    def test_pound_sign_is_drawn_not_typed(self):
        html = MoneyInput(attrs={'class': 'form-input'}).render('cost', '12.50')
        self.assertIn('class="money-input-prefix" aria-hidden="true">£</span>', html)
        self.assertIn('type="number"', html)
        self.assertIn('value="12.50"', html)


class HealthFormTests(TestCase):
    def test_horse_comes_first_with_its_own_empty_label(self):
        for form_class in (VaccinationForm, FarrierVisitForm):
            with self.subTest(form=form_class.__name__):
                form = form_class()
                self.assertEqual(next(iter(form.fields)), 'horse')
                self.assertEqual(form.fields['horse'].empty_label, 'Choose a horse…')

    def test_cost_is_a_money_box(self):
        self.assertIsInstance(VaccinationForm().fields['cost'].widget, MoneyInput)


class SettingsSectionTests(TestCase):
    def test_every_group_is_a_section_with_a_menu_link(self):
        self.client.force_login(make_admin())
        body = self.client.get(reverse('app_settings')).content.decode()
        self.assertIn('x-data="settingsSections"', body)
        for sid in ('business', 'people', 'yard', 'billing', 'you', 'data'):
            with self.subTest(section=sid):
                self.assertIn(f'<section id="{sid}" data-settings-section', body)
                self.assertIn(f'href="#{sid}" class="settings-nav-link"', body)
        self.assertIn('class="settings-save-bar"', body)

    def test_menu_lists_only_the_groups_the_user_can_see(self):
        self.client.force_login(make_user_with_access('plain', horses='view'))
        body = self.client.get(reverse('app_settings')).content.decode()
        self.assertIn('href="#you" class="settings-nav-link"', body)
        self.assertNotIn('href="#business"', body)
        self.assertNotIn('href="#billing"', body)


class SettingsCompactTests(TestCase):
    """The compact Settings page: a menu that reaches each card, sites that
    fold, a way back from each page opened from it, and forms that return."""

    def setUp(self):
        self.admin = make_admin()
        self.client.force_login(self.admin)

    def page(self):
        return self.client.get(reverse('app_settings')).content.decode()

    def test_each_menu_sublink_points_at_a_card_on_the_page(self):
        import re
        body = self.page()
        targets = re.findall(r'href="#([\w-]+)" class="settings-nav-sublink"', body)
        self.assertGreaterEqual(len(targets), 9)
        for target in targets:
            with self.subTest(target=target):
                self.assertIn(f'id="{target}"', body)

    def test_a_yard_with_one_site_opens_on_its_locations(self):
        from core.models import Location
        Location.objects.create(name='Top Field', site='Home')
        body = self.page()
        self.assertIn('<details class="settings-fold" x-data="settingsFold" data-fold-key="site:Home" open>', body)
        self.assertIn('1 location</span>', body)

    def test_with_more_sites_each_site_is_folded(self):
        from core.models import Location
        for site in ('Home', 'Away'):
            for name in ('Top Field', 'Low Field'):
                Location.objects.create(name=name, site=site)
        body = self.page()
        for site in ('Home', 'Away'):
            self.assertIn(f'<details class="settings-fold" x-data="settingsFold" data-fold-key="site:{site}">', body)
        self.assertIn('2 locations</span>', body)
        # One tick box, one action set: every location is on the page once.
        self.assertEqual(body.count('aria-label="Edit Top Field"'), 2)

    def test_a_site_name_is_escaped_in_the_fold_key(self):
        from core.models import Location
        Location.objects.create(name='Top Field', site='Alicky\'s "A" <b>')
        body = self.page()
        self.assertNotIn('<b>', body.split('data-fold-key="site:')[1].split('>')[0])
        self.assertIn('data-fold-key="site:Alicky&#x27;s &quot;A&quot; &lt;b&gt;"', body)

    def test_pages_opened_from_settings_have_the_way_back(self):
        from core.models import RateType
        rate = RateType.objects.create(name='Grass', daily_rate=5)
        pages = {
            reverse('user_create'): 'set-users',
            reverse('user_update', args=[self.admin.pk]): 'set-users',
            reverse('role_create'): 'set-roles',
            reverse('rate_type_create'): 'set-rates',
            reverse('rate_type_update', args=[rate.pk]): 'set-rates',
            reverse('service_item_create'): 'services',
            reverse('audit_log'): 'set-system',
            reverse('background_tasks'): 'set-system',
            reverse('database_check'): 'set-system',
            reverse('recently_deleted'): 'you',
            reverse('account_security'): 'you',
            reverse('feed_stock_clear'): 'data',
        }
        settings_page = self.page()
        for address, group in pages.items():
            with self.subTest(address=address):
                response = self.client.get(address)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f'href="/settings/#{group}" class="settings-back')
                # The place it goes back to is on the Settings page.
                self.assertIn(f'id="{group}"', settings_page)

    def test_a_form_opened_from_settings_returns_to_settings(self):
        from billing.models import ServiceProvider
        from core.models import Location
        from health.models import VaccinationType
        provider = ServiceProvider.objects.create(name='Hill Vets', provider_type='vet')
        vaccine = VaccinationType.objects.create(name='Flu', interval_months=6, reminder_days_before=14)
        field = Location.objects.create(name='Top Field', site='Home')
        cases = [
            (reverse('provider_update', args=[provider.pk]),
             {'name': 'Hill Vets', 'provider_type': 'vet', 'is_active': 'on'},
             '/settings/#set-providers', reverse('provider_list')),
            (reverse('vaccination_type_update', args=[vaccine.pk]),
             {'name': 'Flu', 'interval_months': 6, 'reminder_days_before': 14, 'is_active': 'on'},
             '/settings/#set-vaccinations', reverse('vaccination_type_list')),
            (reverse('location_update', args=[field.pk]),
             {'name': 'Top Field', 'site': 'Home', 'usage': field.usage},
             '/settings/#set-locations', reverse('location_detail', args=[field.pk])),
        ]
        settings_page = self.page()
        for address, data, back, usual in cases:
            with self.subTest(address=address):
                # The Settings page links to the form with the marker.
                self.assertIn(f'href="{address}?from=settings"', settings_page)
                opened = self.client.get(address + '?from=settings')
                self.assertContains(opened, f'href="{back}" class="settings-back')
                self.assertContains(opened, f'<a href="{back}" class="btn-secondary">Cancel</a>')
                self.assertRedirects(
                    self.client.post(address + '?from=settings', data), back, fetch_redirect_response=False)
                # Opened from its own list, it goes where it always went.
                self.assertNotContains(self.client.get(address), 'settings-back')
                self.assertRedirects(self.client.post(address, data), usual, fetch_redirect_response=False)
                # The marker is a fixed word. An address in its place does nothing.
                self.assertRedirects(
                    self.client.post(address + '?from=https://example.com/', data), usual,
                    fetch_redirect_response=False)

    def test_the_built_stylesheet_has_the_classes_the_page_uses(self):
        # static/css/styles.css is built from input.css and committed. A
        # class in the template that is not in the built file draws nothing:
        # .btn-sm was used on 40 buttons and defined nowhere.
        from pathlib import Path

        from django.conf import settings
        css = (Path(settings.BASE_DIR) / 'static' / 'css' / 'styles.css').read_text()
        for name in ('btn-sm', 'settings-fold', 'settings-row-main', 'settings-nav-sublink',
                     'settings-back', 'settings-label', 'settings-row-state', 'settings-group'):
            with self.subTest(name=name):
                self.assertIn(f'.{name}', css)
