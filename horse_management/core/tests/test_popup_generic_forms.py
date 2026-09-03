"""Tests for PopupFormMixin + includes/popup_form.html: the health quick
actions, Charge, and Owner/Location edit served inside the pop-up sheet.

Each view keeps its full page outside the sheet; inside it (HX-Target:
popup-body) it renders the generic partial, re-renders it on errors, and
answers a valid save with 204 + popup:saved.
"""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import ExtraCharge
from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin, make_viewer
from health.models import (
    FarrierVisit, MedicalCondition, VaccinationType, Vaccination,
    VetVisit, WormEggCount, WormingTreatment,
)

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}


def _first_choice(form_class, field_name, **form_kwargs):
    """The first real option of a select field (skips the blank choice)."""
    form = form_class(**form_kwargs)
    for value, _label in form.fields[field_name].choices:
        if value not in ('', None):
            return value
    raise AssertionError(f'{form_class.__name__}.{field_name} has no choices')


class PopupGenericFormTestCase(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.horse = Horse.objects.create(name='Dobbin', sex='mare')
        self.owner = Owner.objects.create(name='Jo Bloggs')
        self.location = Location.objects.create(name='Top Field', site='Main')
        self.rate = RateType.objects.create(name='Full', daily_rate=10)
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate, start_date=self.today - timedelta(days=3),
        )
        self.vax_type = VaccinationType.objects.create(name='Flu', interval_months=6)
        self.client.force_login(make_admin())

    # (url, valid POST data, model, invalid override) per target
    def _targets(self):
        from billing.forms import ExtraChargeForm
        from health.forms import (
            FarrierVisitForm, MedicalConditionForm, WormEggCountForm,
        )
        from core.forms import LocationForm

        q = f'?horse={self.horse.pk}'
        today = self.today.isoformat()
        return [
            ('vaccination_create', q, Vaccination, {
                'horse': self.horse.pk, 'vaccination_type': self.vax_type.pk,
                'date_given': today,
            }, {'date_given': ''}),
            ('farrier_create', q, FarrierVisit, {
                'horse': self.horse.pk, 'date': today,
                'work_done': _first_choice(FarrierVisitForm, 'work_done'), 'cost': '25',
            }, {'cost': ''}),
            ('worming_create', q, WormingTreatment, {
                'horse': self.horse.pk, 'date': today, 'product_name': 'Equest',
            }, {'product_name': ''}),
            ('egg_count_create', q, WormEggCount, {
                'horse': self.horse.pk, 'date': today, 'count': '150',
                'sample_type': _first_choice(WormEggCountForm, 'sample_type'),
            }, {'count': ''}),
            ('condition_create', q, MedicalCondition, {
                'horse': self.horse.pk, 'name': 'Mud fever',
                'status': _first_choice(MedicalConditionForm, 'status'),
            }, {'name': ''}),
            ('vet_visit_create', q, VetVisit, {
                'horse': self.horse.pk, 'date': today, 'reason': 'Lameness check', 'cost': '40',
            }, {'reason': ''}),
            ('charge_create', q, ExtraCharge, {
                'horse': self.horse.pk, 'owner': self.owner.pk,
                'charge_type': _first_choice(ExtraChargeForm, 'charge_type'),
                'date': today, 'description': 'Rug wash', 'amount': '12.50',
            }, {'amount': ''}),
            (('owner_update', [self.owner.pk]), '', Owner, {
                'name': 'Jo Bloggs-Smith', 'email': '', 'phone': '', 'address': '',
                'account_code': '', 'notes': '',
            }, {'name': ''}),
            (('location_update', [self.location.pk]), '', Location, {
                'name': 'Top Field East', 'site': 'Main',
                'usage': _first_choice(LocationForm, 'usage'), 'description': '', 'capacity': '',
            }, {'name': ''}),
        ]

    @staticmethod
    def _url(spec, query):
        if isinstance(spec, tuple):
            return reverse(spec[0], args=spec[1]) + query
        return reverse(spec) + query


class PopupGenericFormTests(PopupGenericFormTestCase):

    def test_popup_request_gets_the_generic_partial(self):
        for spec, query, model, data, bad in self._targets():
            url = self._url(spec, query)
            with self.subTest(url=url):
                response = self.client.get(url, **POPUP)
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, 'includes/popup_form.html')
                self.assertNotContains(response, '<html')
                self.assertContains(response, f'hx-post="{url}"')
                self.assertContains(response, 'hx-target="#popup-body"')
                self.assertContains(response, 'popup-footer')
                self.assertContains(response, 'Open the full form')
                if query:
                    # ?horse= deep link: the picker becomes a hidden field
                    self.assertContains(response, f'<input type="hidden" name="horse" value="{self.horse.pk}">')
                    self.assertNotContains(response, '<select name="horse"')

    def test_plain_request_still_gets_the_full_page(self):
        for spec, query, model, data, bad in self._targets():
            url = self._url(spec, query)
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertTemplateNotUsed(response, 'includes/popup_form.html')
                self.assertContains(response, '<html')
                self.assertNotContains(response, 'hx-post=')

    def test_valid_save_answers_204_with_trigger(self):
        for spec, query, model, data, bad in self._targets():
            url = self._url(spec, query)
            with self.subTest(url=url):
                before = model.objects.count()
                response = self.client.post(url, data, **POPUP)
                self.assertEqual(response.status_code, 204, getattr(response, 'content', b'')[:300])
                self.assertEqual(response['HX-Trigger'], 'popup:saved')
                if isinstance(spec, tuple):
                    self.assertEqual(model.objects.count(), before)
                    self.assertEqual(model.objects.get(pk=spec[1][0]).name, data['name'])
                else:
                    self.assertEqual(model.objects.count(), before + 1)
                    self.assertEqual(model.objects.latest('pk').horse, self.horse)

    def test_invalid_post_re_renders_in_the_sheet(self):
        for spec, query, model, data, bad in self._targets():
            url = self._url(spec, query)
            with self.subTest(url=url):
                before = model.objects.count()
                response = self.client.post(url, {**data, **bad}, **POPUP)
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, 'includes/popup_form.html')
                self.assertContains(response, 'This field is required')
                self.assertNotIn('HX-Trigger', response)
                self.assertEqual(model.objects.count(), before)

    def test_full_page_save_still_redirects(self):
        url = reverse('vaccination_create') + f'?horse={self.horse.pk}'
        response = self.client.post(url, {
            'horse': self.horse.pk, 'vaccination_type': self.vax_type.pk,
            'date_given': self.today.isoformat(),
        })
        self.assertRedirects(response, reverse('horse_detail', args=[self.horse.pk]))

    def test_breeding_create_serves_the_sheet(self):
        url = reverse('breeding_create') + f'?horse={self.horse.pk}'
        response = self.client.get(url, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'includes/popup_form.html')

    def test_viewer_is_refused_in_the_sheet(self):
        self.client.force_login(make_viewer())
        # Viewer role: health is full access, charges/owners/locations are view-only.
        self.assertEqual(self.client.get(reverse('charge_create'), **POPUP).status_code, 403)
        self.assertEqual(
            self.client.get(reverse('owner_update', args=[self.owner.pk]), **POPUP).status_code, 403
        )
        self.assertEqual(self.client.get(reverse('vaccination_create'), **POPUP).status_code, 200)

    def test_multipart_forms_declare_the_encoding(self):
        response = self.client.get(reverse('charge_create'), **POPUP)
        self.assertContains(response, 'hx-encoding="multipart/form-data"')
        response = self.client.get(reverse('vaccination_create'), **POPUP)
        self.assertNotContains(response, 'hx-encoding=')


class PopupGenericTriggerTests(PopupGenericFormTestCase):

    def test_horse_detail_quick_actions_open_the_sheet(self):
        response = self.client.get(reverse('horse_detail', args=[self.horse.pk]))
        for title in (
            'Vaccination for Dobbin', 'Farrier visit for Dobbin', 'Worming for Dobbin',
            'Egg count for Dobbin', 'Condition for Dobbin', 'Vet visit for Dobbin',
            'Breeding record for Dobbin', 'Charge for Dobbin',
        ):
            with self.subTest(title=title):
                self.assertContains(response, f'data-popup-title="{title}"')
        # The deep link is the same URL for href and hx-get
        vax = reverse('vaccination_create') + f'?horse={self.horse.pk}'
        self.assertContains(response, f'href="{vax}" hx-get="{vax}"')

    def test_health_dashboard_add_buttons_open_the_sheet(self):
        for tab, title in (
            ('vaccinations', 'Add vaccination'), ('farrier', 'Add farrier visit'),
            ('worming', 'Add worming'), ('egg_counts', 'Add egg count'),
            ('conditions', 'Add condition'), ('vet_visits', 'Add vet visit'),
        ):
            with self.subTest(tab=tab):
                response = self.client.get(reverse('health_dashboard'), {'type': tab})
                self.assertContains(response, f'data-popup-title="{title}"')

    def test_owner_and_location_edit_open_the_sheet(self):
        for name, args, title in (
            ('owner_detail', [self.owner.pk], 'Edit Jo Bloggs'),
            ('owner_list', [], 'Edit Jo Bloggs'),
            ('location_detail', [self.location.pk], 'Edit Top Field'),
            ('location_list', [], 'Edit Top Field'),
        ):
            with self.subTest(page=name):
                response = self.client.get(reverse(name, args=args))
                self.assertContains(response, f'data-popup-title="{title}"')
