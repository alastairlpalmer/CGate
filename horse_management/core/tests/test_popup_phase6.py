"""Phase 6 of the pop-up sheet: "Save & add another" inside the sheet and
the styled confirm dialog rendered once per page."""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin
from health.models import Vaccination, VaccinationType

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}


class Phase6TestCase(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.horse = Horse.objects.create(name='Dobbin')
        owner = Owner.objects.create(name='Jo Bloggs')
        self.location = location = Location.objects.create(name='Top Field', site='Main')
        rate = RateType.objects.create(name='Full', daily_rate=10)
        Placement.objects.create(
            horse=self.horse, owner=owner, location=location, rate_type=rate,
            start_date=self.today - timedelta(days=3),
        )
        self.vax_type = VaccinationType.objects.create(name='Flu', interval_months=6)
        self.client.force_login(make_admin())


class SaveAndAddAnotherTests(Phase6TestCase):

    def test_create_sheet_offers_save_and_add_another(self):
        url = reverse('vaccination_create') + f'?horse={self.horse.pk}'
        response = self.client.get(url, **POPUP)
        self.assertContains(response, 'name="save_and_add"')
        self.assertContains(response, 'Save &amp; add another')

    def test_edit_sheet_does_not(self):
        vax = Vaccination.objects.create(
            horse=self.horse, vaccination_type=self.vax_type, date_given=self.today,
        )
        response = self.client.get(reverse('vaccination_update', args=[vax.pk]), **POPUP)
        self.assertNotContains(response, 'name="save_and_add"')

    def test_save_and_add_another_saves_and_returns_a_clean_form(self):
        url = reverse('vaccination_create') + f'?horse={self.horse.pk}'
        response = self.client.post(url, {
            'horse': self.horse.pk, 'vaccination_type': self.vax_type.pk,
            'date_given': self.today.isoformat(), 'batch_number': 'B-1',
            'save_and_add': '1',
        }, **POPUP)
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertTemplateUsed(response, 'includes/popup_form.html')
        self.assertNotIn('HX-Trigger', response)
        self.assertEqual(Vaccination.objects.filter(horse=self.horse).count(), 1)
        # A fresh form, flagged so the sheet does not treat it as unsaved edits
        self.assertContains(response, 'Saved. Add another below.')
        self.assertContains(response, 'data-popup-fresh')
        self.assertNotContains(response, 'value="B-1"')
        self.assertContains(response, f'<input type="hidden" name="horse" value="{self.horse.pk}">')
        # Today is prefilled again on the fresh form
        self.assertContains(response, f'value="{self.today.isoformat()}"')

    def test_plain_save_still_closes_the_sheet(self):
        url = reverse('vaccination_create') + f'?horse={self.horse.pk}'
        response = self.client.post(url, {
            'horse': self.horse.pk, 'vaccination_type': self.vax_type.pk,
            'date_given': self.today.isoformat(),
        }, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response['HX-Trigger'], 'popup:saved')

    def test_invalid_save_and_add_re_renders_with_errors_and_saves_nothing(self):
        url = reverse('vaccination_create') + f'?horse={self.horse.pk}'
        response = self.client.post(url, {
            'horse': self.horse.pk, 'vaccination_type': '',
            'date_given': self.today.isoformat(), 'save_and_add': '1',
        }, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'This field is required')
        self.assertNotContains(response, 'data-popup-fresh')
        self.assertEqual(Vaccination.objects.count(), 0)

    def test_full_page_ignores_the_sheet_button(self):
        url = reverse('vaccination_create') + f'?horse={self.horse.pk}'
        response = self.client.post(url, {
            'horse': self.horse.pk, 'vaccination_type': self.vax_type.pk,
            'date_given': self.today.isoformat(), 'save_and_add': '1',
        })
        # The full page keeps its own "Save & Add Another": back to the same blank form
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Vaccination.objects.count(), 1)


class ConfirmSheetTests(Phase6TestCase):

    def test_confirm_dialog_rendered_once_for_signed_in_users(self):
        response = self.client.get(reverse('horse_detail', args=[self.horse.pk]))
        self.assertContains(response, 'id="confirm-panel"', count=1)
        self.assertContains(response, 'role="alertdialog"', count=1)

    def test_confirm_dialog_not_rendered_for_anonymous(self):
        self.client.logout()
        response = self.client.get(reverse('login'))
        self.assertNotContains(response, 'id="confirm-panel"')

    def test_hx_confirm_still_in_the_markup(self):
        """The styled dialog intercepts htmx:confirm, so the questions must
        stay on the elements for the interception to have anything to show."""
        # An empty field's edit page carries the archive/delete confirms
        empty = Location.objects.create(name='Empty Field', site='Main')
        response = self.client.get(reverse('location_update', args=[empty.pk]))
        self.assertContains(response, 'hx-confirm=')
