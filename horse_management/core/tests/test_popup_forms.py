"""Tests for the shared pop-up sheet (includes/_popup_sheet.html) and the
Move form it loads.

The sheet asks for a view with ``HX-Target: popup-body``; the view then
returns the form partial on its own, answers a valid save with 204 +
``HX-Trigger: popup:saved``, and re-renders the partial (status 200, so
htmx swaps it) when the form or the placement service rejects the move.
Boosted navigations also carry ``HX-Request: true`` but target
#main-content, so they must still get the full page.
"""

from datetime import timedelta

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin, make_viewer

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}
BOOSTED = {
    'HTTP_HX_REQUEST': 'true',
    'HTTP_HX_BOOSTED': 'true',
    'HTTP_HX_TARGET': 'main-content',
}


class MoveFixtureTestCase(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.owner = Owner.objects.create(name='Jo Bloggs')
        self.location = Location.objects.create(name='Top Field', site='Main')
        self.other_location = Location.objects.create(name='Bottom Field', site='Main')
        self.rate = RateType.objects.create(name='Full', daily_rate=10)
        self.horse = Horse.objects.create(name='ALIHUNTER')
        self.placement = Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate, start_date=self.today - timedelta(days=30),
        )
        self.url = reverse('horse_move', args=[self.horse.pk])
        self.client.force_login(make_admin())

    def _move_data(self, **overrides):
        data = {
            'new_location': self.other_location.pk,
            'move_date': self.today.isoformat(),
            'notes': '',
        }
        data.update(overrides)
        return data


class MovePopupGetTests(MoveFixtureTestCase):

    def test_popup_request_gets_the_form_only(self):
        response = self.client.get(self.url, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'horses/partials/move_form.html')
        self.assertTemplateNotUsed(response, 'base.html')
        self.assertNotContains(response, '<html')
        # Posts back into the sheet, never the page
        self.assertContains(response, f'hx-post="{self.url}"')
        self.assertContains(response, 'hx-target="#popup-body"')
        self.assertContains(response, 'hx-push-url="false"')
        # The compact "where is it now" line replaces the full page's summary card
        self.assertContains(response, 'Now at')
        self.assertContains(response, 'Top Field')

    def test_today_is_the_default_date_choice(self):
        response = self.client.get(self.url, **POPUP)
        self.assertContains(response, 'pickDate: false')
        self.assertContains(response, f'value="{self.today.isoformat()}"')

    def test_boosted_navigation_still_gets_the_full_page(self):
        response = self.client.get(self.url, **BOOSTED)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'horses/horse_move.html')
        self.assertContains(response, '<html')
        self.assertNotContains(response, 'hx-post=')

    def test_plain_request_gets_the_full_page(self):
        response = self.client.get(self.url)
        self.assertTemplateUsed(response, 'horses/horse_move.html')
        self.assertContains(response, '<html')
        self.assertContains(response, 'form-footer')
        self.assertNotContains(response, 'popup-footer')

    def test_viewer_is_refused(self):
        self.client.force_login(make_viewer())
        response = self.client.get(self.url, **POPUP)
        self.assertEqual(response.status_code, 403)


class MovePopupPostTests(MoveFixtureTestCase):

    def test_valid_save_answers_204_with_trigger(self):
        response = self.client.post(self.url, self._move_data(), **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response['HX-Trigger'], 'popup:saved')
        self.assertEqual(response.content, b'')

        current = self.horse.placements.get(end_date__isnull=True)
        self.assertEqual(current.location, self.other_location)
        self.placement.refresh_from_db()
        self.assertIsNotNone(self.placement.end_date)

    def test_success_toast_shows_on_the_refresh(self):
        self.client.post(self.url, self._move_data(), **POPUP)
        follow_up = self.client.get(reverse('horse_list'))
        texts = [str(m) for m in get_messages(follow_up.wsgi_request)]
        self.assertIn('ALIHUNTER moved successfully.', texts)

    def test_invalid_form_re_renders_in_the_sheet(self):
        response = self.client.post(self.url, self._move_data(new_location=''), **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'horses/partials/move_form.html')
        self.assertNotContains(response, '<html')
        self.assertContains(response, 'This field is required')
        self.assertNotIn('HX-Trigger', response)
        self.assertEqual(self.horse.placements.count(), 1)

    def test_service_error_shows_inline_in_the_sheet(self):
        too_early = (self.placement.start_date - timedelta(days=1)).isoformat()
        response = self.client.post(self.url, self._move_data(move_date=too_early), **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'horses/partials/move_form.html')
        self.assertContains(response, 'Move date must be after')
        # A non-today date re-opens the date picker
        self.assertContains(response, 'pickDate: true')
        self.assertContains(response, f'value="{too_early}"')
        self.assertEqual(self.horse.placements.count(), 1)

    def test_service_error_shows_inline_on_the_full_page(self):
        """Regression: the full page used to crash (NameError) on a
        service ValidationError instead of showing it."""
        too_early = (self.placement.start_date - timedelta(days=1)).isoformat()
        response = self.client.post(self.url, self._move_data(move_date=too_early))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'horses/horse_move.html')
        self.assertContains(response, 'Move date must be after')

    def test_full_page_save_redirects_to_same_origin_next(self):
        detail = reverse('horse_detail', args=[self.horse.pk])
        response = self.client.post(self.url, self._move_data(next=detail))
        self.assertRedirects(response, detail)

    def test_full_page_save_ignores_offsite_next(self):
        response = self.client.post(
            self.url, self._move_data(next='https://evil.example/phish')
        )
        self.assertRedirects(response, reverse('horse_list'))

    def test_full_page_save_defaults_to_the_horse_list(self):
        response = self.client.post(self.url, self._move_data())
        self.assertRedirects(response, reverse('horse_list'))


class PopupSheetRenderingTests(MoveFixtureTestCase):

    def test_sheet_rendered_once_per_page(self):
        response = self.client.get(reverse('horse_list'))
        self.assertContains(response, 'id="popup-body"', count=1)
        self.assertContains(response, 'id="popup-skeleton"', count=1)
        self.assertContains(response, 'js/popup.js')

    def test_sheet_not_rendered_for_anonymous(self):
        self.client.logout()
        response = self.client.get(reverse('login'))
        self.assertNotContains(response, 'id="popup-body"')

    def test_move_triggers_open_the_sheet(self):
        for name, args, query in (
            ('horse_list', [], ''),
            ('horse_detail', [self.horse.pk], ''),
            ('horse_list', [], '?tab=movements'),  # the movement log moved here
            ('location_detail', [self.location.pk], ''),
        ):
            with self.subTest(page=name):
                response = self.client.get(reverse(name, args=args) + query)
                self.assertContains(response, 'data-popup-title="Move ALIHUNTER"')
                self.assertContains(response, f'hx-get="{self.url}')
                # The plain href survives for no-JS, middle-click and bookmarks
                self.assertContains(response, f'href="{self.url}')
