"""The bulk action bar opens its form in the shared pop-up sheet.

The old bar kept its own modal, appended the selected horse ids from
JavaScript and reloaded the page on success. The Locations detail page
had a second, departure-only bar. Now:

1. The form endpoint takes the selected ids in the query string and
   renders them as hidden inputs, so a re-render after a validation error
   still carries the selection.
2. A successful apply answers 204 + ``HX-Trigger: popup:saved`` — the
   contract every pop-up form uses (static/js/popup.js).
3. The Locations detail page includes the same bar as the Horses list,
   with every action, not just a departure date.
"""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin


class BulkActionPopupTests(TestCase):
    def setUp(self):
        self.client.force_login(make_admin(username='bulk-popup'))
        self.today = timezone.localdate()
        self.owner = Owner.objects.create(name='Popup Owner')
        self.location = Location.objects.create(name='Popup Field', site='Main')
        self.rate = RateType.objects.create(name='Full', daily_rate=10)
        self.horses = [
            Horse.objects.create(name=f'POPUP{i}') for i in range(2)
        ]
        for horse in self.horses:
            Placement.objects.create(
                horse=horse, owner=self.owner, location=self.location,
                rate_type=self.rate, start_date=self.today - timedelta(days=10),
            )

    def _ids(self):
        return [str(h.pk) for h in self.horses]

    def test_form_carries_selected_horse_ids_as_hidden_inputs(self):
        response = self.client.get(
            reverse('bulk_health_form'),
            {'action_type': 'actual_departure', 'horse_ids': self._ids() + ['abc']},
        )
        self.assertEqual(response.status_code, 200)
        for horse in self.horses:
            self.assertContains(
                response,
                f'<input type="hidden" name="horse_ids" value="{horse.pk}">',
                html=True,
            )
        self.assertNotContains(response, 'value="abc"')
        # Posts back into the sheet, not a modal of its own
        self.assertContains(response, 'hx-target="#popup-body"')
        self.assertContains(response, 'For 2 horses:')
        self.assertContains(response, 'POPUP0, POPUP1')
        self.assertContains(response, 'Apply to 2 horses')

    def test_validation_error_rerender_keeps_the_selection(self):
        response = self.client.post(
            reverse('bulk_health_apply'),
            {
                'action_type': 'actual_departure',
                'horse_ids': self._ids(),
                'date': 'not-a-date',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('HX-Trigger', response)
        for horse in self.horses:
            self.assertContains(
                response,
                f'<input type="hidden" name="horse_ids" value="{horse.pk}">',
                html=True,
            )

    def test_success_answers_popup_saved(self):
        response = self.client.post(
            reverse('bulk_health_apply'),
            {
                'action_type': 'actual_departure',
                'horse_ids': self._ids(),
                'date': self.today.isoformat(),
            },
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response['HX-Trigger'], 'popup:saved')
        for horse in self.horses:
            horse.refresh_from_db()
            self.assertFalse(horse.is_active)

    def test_location_detail_uses_the_shared_bulk_bar(self):
        response = self.client.get(
            reverse('location_detail', args=[self.location.pk])
        )
        self.assertEqual(response.status_code, 200)
        # Every action, not just a departure date
        for value in ('move', 'expected_departure', 'actual_departure',
                      'vaccination', 'farrier'):
            self.assertContains(response, f'value="{value}"')
        self.assertContains(response, 'data-popup-accent')
        self.assertNotContains(response, 'id="departure-form"')

    def test_apply_button_opens_the_sheet_on_horse_list_too(self):
        response = self.client.get(reverse('horse_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'hx-target="#popup-body"')
        self.assertContains(response, 'data-popup-accent')
        self.assertNotContains(response, 'bulk-form-container')
