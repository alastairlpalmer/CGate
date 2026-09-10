"""Locations on a phone: the map page and its sheet.

On a phone the Locations page is the map itself
(templates/locations/_phone_map.html), with a three-height sheet over it
holding the list and, once a location is picked, that location's detail
(core.views.locations.location_preview). Everything from `lg` up is the
tabs and cards it always was, so the tests here check what the phone
layout needs and that the desktop page is still served alongside it.
"""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.dashboard.board import REST_READY_DAYS, rest_state
from core.models import (
    DashboardPreference,
    Horse,
    Location,
    LocationUsagePeriod,
    Owner,
    Placement,
    RateType,
)
from core.roles_testutils import make_admin, make_user_with_access

SHEET = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'loc-sheet-detail'}
BOOSTED = {
    'HTTP_HX_REQUEST': 'true',
    'HTTP_HX_BOOSTED': 'true',
    'HTTP_HX_TARGET': 'main-content',
}

SQUARE = {
    'type': 'Polygon',
    'coordinates': [[
        [-0.245, 51.062], [-0.244, 51.062],
        [-0.244, 51.063], [-0.245, 51.063], [-0.245, 51.062],
    ]],
}


class RestStateTests(TestCase):
    """Where a location sits in the rotation — the phone map's colours."""

    def test_over_its_limit_beats_everything_else(self):
        self.assertEqual(
            rest_state(count=14, capacity=12, holds_horses=True, rest_days=None),
            'over',
        )

    def test_horses_on_it_read_as_grazing(self):
        self.assertEqual(
            rest_state(count=3, capacity=12, holds_horses=True, rest_days=None),
            'grazing',
        )

    def test_a_fortnight_is_the_line_between_recovering_and_rested(self):
        recovering = rest_state(
            count=0, capacity=None, holds_horses=False,
            rest_days=REST_READY_DAYS - 1,
        )
        rested = rest_state(
            count=0, capacity=None, holds_horses=False, rest_days=REST_READY_DAYS,
        )
        self.assertEqual(recovering, 'recovering')
        self.assertEqual(rested, 'rested')

    def test_empty_grazing_ground_is_not_the_same_as_rested(self):
        """Nobody marked it rested, so the map must not claim it is."""
        self.assertEqual(
            rest_state(count=0, capacity=8, holds_horses=True, rest_days=None),
            'empty',
        )

    def test_ground_that_is_not_grazing_at_all_is_left_to_its_land_use(self):
        self.assertEqual(
            rest_state(count=0, capacity=None, holds_horses=False, rest_days=None),
            'other',
        )

    def test_no_capacity_recorded_cannot_be_over_capacity(self):
        self.assertEqual(
            rest_state(count=30, capacity=None, holds_horses=True, rest_days=None),
            'grazing',
        )


class PhoneMapFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.owner = Owner.objects.create(name='Milly Hine')
        cls.rate = RateType.objects.create(name='Grass', daily_rate=5)

        # Somerford is mapped; Colgate is not, and sorts first by name.
        cls.mapped = Location.objects.create(
            name='Red Hatches', site='Somerford', capacity=15,
            usage=Location.Usage.HORSES,
            latitude='51.0625', longitude='-0.2445', boundary=SQUARE,
            boundary_updated_at=timezone.now(),
        )
        cls.rested = Location.objects.create(
            name='Hawkhill', site='Somerford', capacity=12,
            usage=Location.Usage.RESTED,
            latitude='51.0635', longitude='-0.2455', boundary=SQUARE,
            boundary_updated_at=timezone.now(),
        )
        LocationUsagePeriod.objects.create(
            location=cls.rested, usage=Location.Usage.RESTED,
            start_date=cls.today - timedelta(days=21),
        )
        cls.unmapped = Location.objects.create(
            name='Stables top yard', site='Somerford', capacity=10,
            usage=Location.Usage.HORSES,
        )
        cls.other_site = Location.objects.create(
            name='Bottom field', site='Colgate', capacity=6,
            usage=Location.Usage.HORSES,
        )

        cls.horse = Horse.objects.create(name='Antoinette', sex=Horse.Sex.MARE)
        Placement.objects.create(
            horse=cls.horse, owner=cls.owner, location=cls.mapped,
            rate_type=cls.rate, start_date=cls.today - timedelta(days=11),
        )
        cls.url = reverse('location_list')


@override_settings(LOCATION_MAPS_ENABLED=True)
class PhoneMapPageTests(PhoneMapFixture):
    def setUp(self):
        self.client.force_login(make_admin())

    def body(self, **params):
        return self.client.get(self.url, params).content.decode()

    def test_the_phone_page_is_served_with_the_desktop_one(self):
        """One response holds both shapes of the site board: the map with
        its sheet on a phone, and the rail beside the map from lg. CSS
        picks; only one of the two ever mounts a map (location_map.js
        waits for its container to have a size)."""
        body = self.body()
        self.assertIn('data-phone-map', body)
        self.assertIn('data-loc-sheet', body)
        self.assertIn('data-site-board', body)

    def test_it_opens_on_a_site_that_has_a_map(self):
        """Colgate sorts first, but landing on the map page and being told
        there is no map is a poor first answer."""
        body = self.body()
        self.assertIn('data-site="Somerford"', body)

    def test_the_url_still_chooses_the_site(self):
        self.assertIn('data-site="Colgate"', self.body(site='Colgate'))

    def test_the_dashboard_preference_is_used_before_the_mapped_site(self):
        user = make_admin(username='pref-user')
        DashboardPreference.objects.update_or_create(
            user=user, defaults={'site': 'Colgate'},
        )
        self.client.force_login(user)
        self.assertIn('data-site="Colgate"', self.body())

    def test_the_selector_says_how_much_of_each_site_is_mapped(self):
        """The pill names the site; the list behind it says, for every
        site including this one, how much of it is drawn — which is what
        you need before you switch, not after."""
        body = self.body()
        self.assertIn('3 locations &middot; 2 mapped', body)
        self.assertIn('1 location &middot; 0 mapped', body)

    def test_the_selector_rides_in_the_app_bar_rather_than_a_row_of_its_own(self):
        """One row of chrome over the map, using the same teleport the
        list filters use."""
        body = self.body()
        self.assertIn('x-teleport="#app-bar-slot"', body)
        self.assertIn('data-loc-action="sites"', body)

    def test_locations_with_no_boundary_are_listed_apart(self):
        body = self.body()
        self.assertIn('Not on the map (<span data-loc-unmapped-count>1</span>)', body)
        self.assertIn('data-unmapped', body)

    def test_a_row_carries_what_the_browser_sorts_and_filters_on(self):
        body = self.body()
        self.assertIn(f'data-pk="{self.rested.pk}"', body)
        self.assertIn('data-state="rested"', body)
        self.assertIn('data-rest="21"', body)
        # No rest record reads as -1, never as "rested nought days".
        self.assertIn('data-rest="-1"', body)

    def test_the_usage_page_is_still_reachable_from_the_sheet(self):
        self.assertIn(f'{self.url}?tab=usage', self.body())

    def test_usage_does_not_build_a_map_it_cannot_show(self):
        self.assertNotIn('data-phone-map', self.body(tab='usage'))

    def test_the_cards_view_drops_both_shapes_of_the_board(self):
        body = self.body(view='cards')
        self.assertNotIn('data-phone-map', body)
        self.assertNotIn('data-site-board', body)


class PhoneMapDisabledTests(PhoneMapFixture):
    @override_settings(LOCATION_MAPS_ENABLED=False)
    def test_without_the_maps_feature_the_phone_keeps_the_cards(self):
        self.client.force_login(make_admin())
        body = self.client.get(self.url).content.decode()
        self.assertNotIn('data-phone-map', body)
        self.assertIn('grid grid-cols-1 md:grid-cols-2', body)


class LocationPreviewTests(PhoneMapFixture):
    def setUp(self):
        self.client.force_login(make_admin())

    def sheet(self, location=None):
        url = reverse('location_preview', args=[(location or self.mapped).pk])
        return self.client.get(url, **SHEET).content.decode()

    def test_only_the_sheet_gets_the_partial(self):
        url = reverse('location_preview', args=[self.mapped.pk])
        page = reverse('location_detail', args=[self.mapped.pk])
        self.assertRedirects(self.client.get(url), page)
        self.assertRedirects(self.client.get(url, **BOOSTED), page)

    def test_the_sheet_request_renders_the_partial(self):
        url = reverse('location_preview', args=[self.mapped.pk])
        response = self.client.get(url, **SHEET)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'locations/partials/preview.html')
        self.assertIn('id="loc-sheet-content"', response.content.decode())
        self.assertNotIn(b'<html', response.content)

    def test_a_missing_location_is_a_404(self):
        response = self.client.get(reverse('location_preview', args=[99999]), **SHEET)
        self.assertEqual(response.status_code, 404)

    def test_a_role_without_locations_cannot_open_it(self):
        self.client.force_login(make_user_with_access('nolocs', locations='hidden'))
        url = reverse('location_preview', args=[self.mapped.pk])
        self.assertNotEqual(self.client.get(url, **SHEET).status_code, 200)

    def test_it_names_the_horses_on_the_ground(self):
        body = self.sheet()
        self.assertIn('Red Hatches', body)
        self.assertIn('Antoinette', body)
        self.assertIn('Milly Hine', body)

    def test_it_says_how_much_room_is_left(self):
        self.assertIn('14 spaces free', self.sheet())

    def test_one_space_left_is_not_called_1_spaces(self):
        for _ in range(13):
            horse = Horse.objects.create(name=f'Filler {_}', sex=Horse.Sex.MARE)
            Placement.objects.create(
                horse=horse, owner=self.owner, location=self.mapped,
                rate_type=self.rate, start_date=self.today,
            )
        self.assertIn('1 space free', self.sheet())

    def test_over_the_limit_says_by_how_many(self):
        for _ in range(15):
            horse = Horse.objects.create(name=f'Extra {_}', sex=Horse.Sex.MARE)
            Placement.objects.create(
                horse=horse, owner=self.owner, location=self.mapped,
                rate_type=self.rate, start_date=self.today,
            )
        body = self.sheet()
        self.assertIn('1 over the limit', body)
        self.assertIn('Over capacity', body)

    def test_a_rested_location_says_how_long_and_since_when(self):
        body = self.sheet(self.rested)
        self.assertIn('Rested 21 days', body)
        self.assertIn('resting since', body)

    def test_the_four_actions_are_all_there(self):
        body = self.sheet()
        for label in ('Move horses', 'Feed out', 'Mark rested', 'Directions'):
            with self.subTest(action=label):
                self.assertIn(label, body)

    def test_mark_rested_opens_the_dated_land_use_form(self):
        """A one-tap write would let a mis-tap in a gateway invent a rest
        period; the form dates it and can backdate it."""
        body = self.sheet()
        self.assertIn(reverse('location_set_usage', args=[self.mapped.pk]), body)
        self.assertIn('hx-target="#popup-body"', body)

    def test_a_location_with_no_coordinates_offers_its_page_not_directions(self):
        body = self.sheet(self.unmapped)
        self.assertNotIn('Directions', body)
        self.assertIn('Location page', body)
