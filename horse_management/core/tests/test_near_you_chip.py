"""Phase 2 of the location mapping plan: the nearest-location chip.

The distance maths is JavaScript (static/js/geo.js, tested with
``node --test``). These tests cover the server half: the payload the
dashboard emits, its gating on the feature flag and the Locations
feature, and the "last opened" markers.
"""

from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import Location, SiteSettings
from core.roles_testutils import make_admin, make_user_with_access


@override_settings(LOCATION_MAPS_ENABLED=True)
class NearYouPayloadTests(TestCase):

    def setUp(self):
        self.client.force_login(make_admin())
        self.a = Location.objects.create(
            name='Grain store field', site='Somerford',
            latitude=Decimal('51.548038'), longitude=Decimal('-2.064611'),
        )
        Location.objects.create(name='No point', site='Somerford')
        Location.objects.create(
            name='Gone', site='Somerford', is_archived=True,
            latitude=Decimal('51.5'), longitude=Decimal('-2.1'),
        )
        SiteSettings.objects.create(
            site='Somerford', latitude=Decimal('51.55'), longitude=Decimal('-2.06'), radius_m=1200,
        )
        SiteSettings.objects.create(site='Old site', latitude=Decimal('51'), longitude=Decimal('-2'))

    def test_payload_carries_located_active_locations_and_site_centres(self):
        response = self.client.get(reverse('dashboard'))
        payload = response.context['near_you']
        self.assertEqual(
            payload['locations'],
            [{'pk': self.a.pk, 'name': 'Grain store field', 'site': 'Somerford',
              'lat': 51.548038, 'lng': -2.064611}],
        )
        self.assertEqual(payload['sites'], [
            {'name': 'Somerford', 'lat': 51.55, 'lng': -2.06, 'radius_m': 1200, 'count': 2},
        ])
        self.assertEqual(payload['near_radius_m'], 150)
        self.assertEqual(payload['urls']['horses'], reverse('horse_list'))
        self.assertEqual(sorted(payload['all_pks']), sorted(
            Location.objects.active().values_list('pk', flat=True)
        ))
        self.assertContains(response, 'id="near-you-data"')
        self.assertContains(response, 'data-testid="near-you"')
        self.assertContains(response, 'x-data="nearYou"')
        # Nothing is rendered server-side: the chip only appears with an answer.
        self.assertNotContains(response, 'near-chip"')

    def test_flag_off_renders_no_chip(self):
        with self.settings(LOCATION_MAPS_ENABLED=False):
            response = self.client.get(reverse('dashboard'))
        self.assertIsNone(response.context['near_you'])
        self.assertNotContains(response, 'near-you-data')

    def test_locations_hidden_renders_no_chip(self):
        self.client.force_login(make_user_with_access('nolocs', dashboard='full', locations='hidden'))
        response = self.client.get(reverse('dashboard'))
        self.assertIsNone(response.context['near_you'])
        self.assertNotContains(response, 'near-you-data')

    def test_near_radius_is_configurable(self):
        with self.settings(LOCATION_NEAR_RADIUS_M=300):
            response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.context['near_you']['near_radius_m'], 300)


class RememberLocationMarkerTests(TestCase):

    def setUp(self):
        self.client.force_login(make_admin())
        self.location = Location.objects.create(name='Grain store field', site='Somerford')

    def test_location_detail_marks_itself(self):
        response = self.client.get(reverse('location_detail', args=[self.location.pk]))
        self.assertContains(response, f'data-remember-location="{self.location.pk}"')
        self.assertContains(response, 'data-remember-name="Grain store field"')

    def test_archived_location_is_not_remembered(self):
        self.location.is_archived = True
        self.location.save()
        response = self.client.get(reverse('location_detail', args=[self.location.pk]))
        self.assertNotContains(response, 'data-remember-location')

    def test_horse_list_filtered_to_one_location_marks_it(self):
        response = self.client.get(reverse('horse_list'), {'group_by': 'location', 'location': self.location.pk})
        self.assertContains(response, f'data-remember-location="{self.location.pk}"')
        response = self.client.get(reverse('horse_list'))
        self.assertNotContains(response, 'data-remember-location')
        response = self.client.get(reverse('horse_list'), {'location': 'abc'})
        self.assertNotContains(response, 'data-remember-location')
