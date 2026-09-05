"""Phase 3 of the location mapping plan: the map tab and the Near you card.

The drawing is JavaScript (static/js/location_map.js); these tests cover
the one place map data is shaped (core.dashboard.board.map_locations),
the badges the partial renders, the Map tab and its empty state, the
widget, and the pin.
"""

import json
from decimal import Decimal
from pathlib import Path

from django.test import TestCase, override_settings
from django.urls import reverse
from shapely.geometry import Point, shape

from core.dashboard import board
from core.dashboard_widgets import WIDGETS_BY_KEY, widget_available
from core.models import DashboardPreference, Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin, make_user_with_access

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'geo'


def concave_l():
    return json.loads((FIXTURES / 'concave_l.geojson').read_text())['features'][0]['geometry']


def place(location, n):
    owner, _ = Owner.objects.get_or_create(name='Owner')
    rate, _ = RateType.objects.get_or_create(name='Grass', defaults={'daily_rate': Decimal('10')})
    for i in range(n):
        horse = Horse.objects.create(name=f'{location.name} {i}')
        Placement.objects.create(horse=horse, owner=owner, location=location, rate_type=rate, start_date='2026-01-01')


class MapLocationsTests(TestCase):

    def setUp(self):
        self.polygon = Location.objects.create(
            name='L field', site='Somerford', capacity=4, boundary=concave_l(),
            latitude=Decimal('51.5'), longitude=Decimal('-2.1'),
        )
        self.circle = Location.objects.create(
            name='Round pen', site='Somerford', capacity=2,
            latitude=Decimal('51.549'), longitude=Decimal('-2.063'),
        )
        self.nothing = Location.objects.create(name='Barn', site='Somerford', capacity=10)
        self.rested = Location.objects.create(
            name='Rest field', site='Somerford', usage='rested', capacity=6,
            latitude=Decimal('51.55'), longitude=Decimal('-2.06'),
        )
        Location.objects.create(
            name='Gone', site='Somerford', is_archived=True,
            latitude=Decimal('51.55'), longitude=Decimal('-2.06'),
        )
        Location.objects.create(name='Other', site='Colgate', latitude=Decimal('52'), longitude=Decimal('-1'))
        place(self.polygon, 4)
        place(self.circle, 1)

    def test_shapes_circle_polygon_and_skipped(self):
        data = board.map_locations('Somerford')
        by_name = {loc['name']: loc for loc in data['locations']}
        self.assertEqual(set(by_name), {'L field', 'Round pen', 'Barn', 'Rest field'})
        self.assertEqual((data['total'], data['located'], data['unlocated'], data['horses']), (4, 3, 1, 5))

        poly = by_name['L field']
        self.assertEqual(poly['kind'], 'polygon')
        self.assertEqual(poly['boundary'], concave_l())
        self.assertIsNone(poly['radius_m'])
        self.assertEqual(poly['state'], 'over')
        self.assertEqual(poly['colour'], '#C0392B')
        self.assertEqual(poly['urls']['horses'], f"{reverse('horse_list')}?group_by=location&location={self.polygon.pk}")

        circle = by_name['Round pen']
        self.assertEqual(circle['kind'], 'circle')
        self.assertEqual(circle['anchor'], [51.549, -2.063])
        self.assertEqual(circle['radius_m'], 23)
        self.assertEqual(circle['state'], 'near')
        self.assertIsNone(circle['boundary'])

        barn = by_name['Barn']
        self.assertIsNone(barn['kind'])
        self.assertIsNone(barn['anchor'])

        rested = by_name['Rest field']
        self.assertFalse(rested['holds_horses'])
        self.assertIsNone(rested['capacity'])
        self.assertIsNone(rested['state'])
        self.assertEqual(rested['colour'], '#6A8990')

    def test_polygon_anchor_is_inside_a_concave_shape(self):
        data = board.map_locations('Somerford')
        poly = [loc for loc in data['locations'] if loc['kind'] == 'polygon'][0]
        geom = shape(poly['boundary'])
        lat, lng = poly['anchor']
        self.assertTrue(geom.contains(Point(lng, lat)))
        self.assertFalse(geom.contains(geom.centroid))

    def test_by_site_matches_per_site(self):
        by_site = board.map_locations_by_site()
        self.assertEqual(set(by_site), {'Somerford', 'Colgate'})
        self.assertEqual(by_site['Somerford'], board.map_locations('Somerford'))

    def test_circle_radius_clamps(self):
        self.assertEqual(board.circle_radius_m(None), 25)
        self.assertEqual(board.circle_radius_m(1), 20)
        self.assertEqual(board.circle_radius_m(100), 70)

    def test_capacity_state_matches_the_ring(self):
        self.assertEqual(board.capacity_state(10, 0), 'over')
        self.assertEqual(board.capacity_state(10, -3), 'over')
        self.assertEqual(board.capacity_state(10, 2), 'near')
        self.assertEqual(board.capacity_state(10, 5), 'ok')
        self.assertIsNone(board.capacity_state(None, None))
        self.assertIsNone(board.capacity_state(0, 0))

    def test_unknown_site_is_empty(self):
        data = board.map_locations('Nowhere')
        self.assertEqual((data['total'], data['located']), (0, 0))


@override_settings(LOCATION_MAPS_ENABLED=True)
class MapTabTests(TestCase):

    def setUp(self):
        self.user = make_admin()
        self.client.force_login(self.user)
        self.a = Location.objects.create(
            name='Grain store field', site='Somerford', capacity=12,
            latitude=Decimal('51.548'), longitude=Decimal('-2.064'),
        )
        self.b = Location.objects.create(name='Barn', site='Somerford', capacity=4)
        self.c = Location.objects.create(name='Elsewhere', site='Colgate')

    def test_tab_is_offered_and_renders_the_full_map(self):
        response = self.client.get(reverse('location_list'))
        self.assertContains(response, '?tab=map')
        response = self.client.get(reverse('location_list') + '?tab=map')
        self.assertEqual(response.context['current_tab'], 'map')
        self.assertEqual(response.context['map_site'], 'Colgate')   # first by name, no preference
        response = self.client.get(reverse('location_list') + '?tab=map&site=Somerford')
        self.assertContains(response, 'data-testid="location-map"')
        self.assertContains(response, 'is-full')
        self.assertContains(response, f'data-map-badge="{self.a.pk}"')
        self.assertNotContains(response, f'data-map-badge="{self.b.pk}"')   # no point: no badge
        self.assertContains(response, 'data-map-label=')
        self.assertContains(response, 'id="map-data-somerford-full"')
        self.assertContains(response, '1 location without coordinates is not drawn')
        self.assertContains(response, 'Somerford')
        self.assertContains(response, '0 horses')
        # Site selector, both sites
        self.assertContains(response, '?tab=map&site=Colgate')
        # Badge is the ring partial at 44 px and links to the horse list
        self.assertContains(response, 'width:44px;height:44px')
        self.assertContains(response, f'group_by=location&amp;location={self.a.pk}')

    def test_default_site_follows_the_dashboard_preference(self):
        pref = DashboardPreference.get_for(self.user)
        pref.site = 'Somerford'
        pref.save()
        response = self.client.get(reverse('location_list') + '?tab=map')
        self.assertEqual(response.context['map_site'], 'Somerford')

    def test_empty_state_when_no_location_has_coordinates(self):
        response = self.client.get(reverse('location_list') + '?tab=map&site=Colgate')
        self.assertContains(response, 'Nothing to draw yet')
        self.assertContains(response, 'Add coordinates to your locations')
        self.assertNotContains(response, 'data-testid="location-map"')

    def test_tab_absent_with_the_flag_off(self):
        with self.settings(LOCATION_MAPS_ENABLED=False):
            response = self.client.get(reverse('location_list'))
            self.assertNotContains(response, '?tab=map')
            response = self.client.get(reverse('location_list') + '?tab=map')
        self.assertEqual(response.context['current_tab'], 'locations')
        self.assertContains(response, 'Grain store field')

    def test_pinned_marker_on_the_badge(self):
        pref = DashboardPreference.get_for(self.user)
        pref.pinned_location = self.a
        pref.save()
        response = self.client.get(reverse('location_list') + '?tab=map&site=Somerford')
        self.assertContains(response, 'location-map-pin')

    def test_polygon_and_circle_mixed_in_one_view(self):
        Location.objects.create(name='L field', site='Somerford', boundary=concave_l(), capacity=3)
        response = self.client.get(reverse('location_list') + '?tab=map&site=Somerford')
        payload = json.loads(response.context['map_payload'] and json.dumps(response.context['map_payload']))
        kinds = sorted(loc['kind'] for loc in payload['locations'] if loc['kind'])
        self.assertEqual(kinds, ['circle', 'polygon'])
        self.assertEqual(payload['located'], 2)

    def test_locations_page_uses_the_ring_partial(self):
        response = self.client.get(reverse('location_list'))
        self.assertContains(response, '0 of 12 spaces used')
        self.assertContains(response, 'width:44px;height:44px', count=3)


@override_settings(LOCATION_MAPS_ENABLED=True)
class NearYouCardTests(TestCase):

    def setUp(self):
        self.user = make_admin()
        self.client.force_login(self.user)
        self.a = Location.objects.create(
            name='Grain store field', site='Somerford', capacity=12,
            latitude=Decimal('51.548'), longitude=Decimal('-2.064'),
        )
        self.b = Location.objects.create(name='Barn', site='Somerford')

    def test_widget_is_registered_and_gated_on_the_flag(self):
        self.assertEqual(WIDGETS_BY_KEY['near_you']['feature'], 'locations')
        self.assertTrue(widget_available(WIDGETS_BY_KEY['near_you']))
        with self.settings(LOCATION_MAPS_ENABLED=False):
            self.assertFalse(widget_available(WIDGETS_BY_KEY['near_you']))
            self.assertNotIn('near_you', DashboardPreference.get_for(self.user).visible_keys())
            response = self.client.get(reverse('app_settings'))
            self.assertNotContains(response, 'data-widget-key="near_you"')
        self.assertIn('near_you', DashboardPreference.get_for(self.user).visible_keys())
        response = self.client.get(reverse('app_settings'))
        self.assertContains(response, 'data-widget-key="near_you"')

    def test_card_renders_with_one_compact_map_per_site(self):
        Location.objects.create(name='Far', site='Colgate', latitude=Decimal('52'), longitude=Decimal('-1'))
        response = self.client.get(reverse('dashboard'))
        card = response.context['near_you_card']
        self.assertEqual(card['site_names'], ['Colgate', 'Somerford'])
        self.assertEqual(card['default_site'], '')
        self.assertEqual(card['site_counts'], {'Colgate': 1, 'Somerford': 2})
        self.assertEqual(card['unlocated'], [{'pk': self.b.pk, 'site': 'Somerford'}])
        self.assertIsNone(card['pinned'])
        self.assertContains(response, 'data-testid="near-you-card"')
        self.assertContains(response, 'id="near-you-card-data"')
        self.assertContains(response, 'id="map-data-somerford-compact"')
        self.assertContains(response, 'id="map-data-colgate-compact"')
        self.assertContains(response, 'is-compact', count=2)
        # The card sits above the Yard board
        self.assertLess(response.content.index(b'near-you-title'), response.content.index(b'yard-board-title'))

    def test_default_site_and_pin(self):
        pref = DashboardPreference.get_for(self.user)
        pref.site = 'Somerford'
        pref.pinned_location = self.a
        pref.save()
        response = self.client.get(reverse('dashboard'))
        card = response.context['near_you_card']
        self.assertEqual(card['default_site'], 'Somerford')
        self.assertEqual(card['pinned'], {'pk': self.a.pk, 'name': 'Grain store field', 'site': 'Somerford'})

    def test_archived_pin_is_dropped(self):
        pref = DashboardPreference.get_for(self.user)
        pref.pinned_location = self.a
        pref.save()
        self.a.is_archived = True
        self.a.save()
        response = self.client.get(reverse('dashboard'))
        self.assertIsNone(response.context['near_you_card']['pinned'])

    def test_card_hidden_when_switched_off_or_flag_off_or_no_locations_feature(self):
        pref = DashboardPreference.get_for(self.user)
        pref.layout = {'near_you': {'visible': False, 'order': 0}}
        pref.save()
        response = self.client.get(reverse('dashboard'))
        self.assertIsNone(response.context['near_you_card'])
        self.assertNotContains(response, 'near-you-card')
        pref.layout = {}
        pref.save()
        with self.settings(LOCATION_MAPS_ENABLED=False):
            response = self.client.get(reverse('dashboard'))
            self.assertIsNone(response.context['near_you_card'])
        self.client.force_login(make_user_with_access('nolocs', dashboard='full', locations='hidden'))
        response = self.client.get(reverse('dashboard'))
        self.assertIsNone(response.context['near_you_card'])

    def test_no_sites_means_no_card(self):
        Location.objects.all().delete()
        response = self.client.get(reverse('dashboard'))
        self.assertIsNone(response.context['near_you_card'])
