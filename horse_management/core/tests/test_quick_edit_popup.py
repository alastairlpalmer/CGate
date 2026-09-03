"""Tests for the Quick Edit form inside the pop-up sheet."""

from datetime import timedelta

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.forms import QuickHorseForm
from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin, make_viewer

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}


class QuickEditTestCase(TestCase):
    def setUp(self):
        self.horse = Horse.objects.create(name='Dobbin', sex='gelding', color='bay')
        self.url = reverse('horse_quick_edit', args=[self.horse.pk])
        self.full_url = reverse('horse_update', args=[self.horse.pk])
        self.client.force_login(make_admin())

    def _data(self, **overrides):
        data = {
            'name': 'Dobbin',
            'sex': 'gelding',
            'color': 'bay',
            'date_of_birth': '',
            'age': '',
            'passport_number': '',
            'has_passport': 'on',
            'notes': '',
            'is_active': 'on',
        }
        data.update(overrides)
        return data


class QuickEditFormTests(TestCase):

    def test_quick_form_is_the_day_to_day_subset(self):
        fields = list(QuickHorseForm.base_fields)
        for name in ('name', 'sex', 'color', 'date_of_birth', 'age',
                     'passport_number', 'has_passport', 'notes', 'is_active'):
            self.assertIn(name, fields)
        for name in ('photo', 'dam_name', 'sire_name', 'breeding'):
            self.assertNotIn(name, fields)


class QuickEditGetTests(QuickEditTestCase):

    def test_popup_request_gets_the_form_only(self):
        response = self.client.get(self.url, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'horses/partials/quick_edit_form.html')
        self.assertNotContains(response, '<html')
        self.assertContains(response, f'hx-post="{self.url}"')
        self.assertContains(response, 'hx-target="#popup-body"')
        self.assertContains(response, 'popup-footer')
        self.assertContains(response, 'value="Dobbin"')
        # Prefilled selects
        self.assertContains(response, '<option value="gelding" selected')
        # The rest lives on the full page, one link away
        self.assertContains(response, f'href="{self.full_url}"')
        self.assertNotContains(response, 'name="photo"')
        self.assertNotContains(response, 'name="breeding"')
        self.assertNotContains(response, 'ownership_shares')

    def test_outside_the_sheet_it_redirects_to_the_full_edit_page(self):
        self.assertRedirects(self.client.get(self.url), self.full_url)
        boosted = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_BOOSTED': 'true', 'HTTP_HX_TARGET': 'main-content'}
        self.assertRedirects(self.client.get(self.url, **boosted), self.full_url)

    def test_viewer_is_refused(self):
        self.client.force_login(make_viewer())
        self.assertEqual(self.client.get(self.url, **POPUP).status_code, 403)


class QuickEditPostTests(QuickEditTestCase):

    def test_valid_save_answers_204_with_trigger(self):
        response = self.client.post(
            self.url, self._data(name='Dobbin II', color='grey', passport_number='GB123'), **POPUP
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response['HX-Trigger'], 'popup:saved')
        self.horse.refresh_from_db()
        self.assertEqual(self.horse.name, 'Dobbin II')
        self.assertEqual(self.horse.color, 'grey')
        self.assertEqual(self.horse.passport_number, 'GB123')
        follow_up = self.client.get(reverse('horse_detail', args=[self.horse.pk]))
        texts = [str(m) for m in get_messages(follow_up.wsgi_request)]
        self.assertIn("Horse 'Dobbin II' updated successfully.", texts)

    def test_untouched_fields_survive(self):
        self.horse.dam_name = 'Daisy'
        self.horse.breeding = 'By Storm out of Daisy'
        self.horse.save()
        self.client.post(self.url, self._data(name='Dobbin II'), **POPUP)
        self.horse.refresh_from_db()
        self.assertEqual(self.horse.dam_name, 'Daisy')
        self.assertEqual(self.horse.breeding, 'By Storm out of Daisy')

    def test_invalid_form_re_renders_in_the_sheet(self):
        response = self.client.post(self.url, self._data(name=''), **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'horses/partials/quick_edit_form.html')
        self.assertNotContains(response, '<html')
        self.assertContains(response, 'This field is required')
        self.assertNotIn('HX-Trigger', response)
        self.horse.refresh_from_db()
        self.assertEqual(self.horse.name, 'Dobbin')

    def test_active_guard_shows_inline(self):
        owner = Owner.objects.create(name='Jo Bloggs')
        location = Location.objects.create(name='Top Field', site='Main')
        rate = RateType.objects.create(name='Full', daily_rate=10)
        Placement.objects.create(
            horse=self.horse, owner=owner, location=location, rate_type=rate,
            start_date=timezone.localdate() - timedelta(days=3),
        )
        data = self._data()
        data.pop('is_active')
        response = self.client.post(self.url, data, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'still has an open placement')
        self.horse.refresh_from_db()
        self.assertTrue(self.horse.is_active)

    def test_post_outside_the_sheet_redirects_without_saving(self):
        response = self.client.post(self.url, self._data(name='Nope'))
        self.assertRedirects(response, self.full_url)
        self.horse.refresh_from_db()
        self.assertEqual(self.horse.name, 'Dobbin')


class QuickEditTriggerTests(QuickEditTestCase):

    def test_edit_triggers_open_the_sheet_and_keep_the_full_page_href(self):
        for name, args in (('horse_list', []), ('horse_detail', [self.horse.pk])):
            with self.subTest(page=name):
                response = self.client.get(reverse(name, args=args))
                self.assertContains(response, 'data-popup-title="Edit Dobbin"')
                self.assertContains(response, f'hx-get="{self.url}"')
                self.assertContains(response, f'href="{self.full_url}"')

    def test_full_edit_page_is_unchanged(self):
        response = self.client.get(self.full_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ownership Shares')
        self.assertContains(response, 'Profile picture')
