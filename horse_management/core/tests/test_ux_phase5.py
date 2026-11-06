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
