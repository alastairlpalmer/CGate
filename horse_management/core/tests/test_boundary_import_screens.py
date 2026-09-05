"""Phase 4b of the location mapping plan: the import screens.

Upload (validation and the error messages), the matching page (rows,
suggestions pre-selected, preview map, notes), and the commit (atomic,
overwrite confirmation, new locations, points gained from shapes).
"""

import json
from decimal import Decimal
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from core.boundary_import import parse_geojson
from core.models import Location, LocationBoundaryHistory
from core.roles_testutils import make_admin, make_user_with_access

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'geo'


def upload(name, filename=None):
    return SimpleUploadedFile(filename or name, (FIXTURES / name).read_bytes(), content_type='application/geo+json')


@override_settings(LOCATION_MAPS_ENABLED=True)
class UploadTests(TestCase):

    def setUp(self):
        self.client.force_login(make_admin())
        Location.objects.create(name='Grain store field', site='Somerford')
        self.url = reverse('boundary_import_upload')

    def test_page_and_entry_points(self):
        response = self.client.get(self.url + '?site=Somerford')
        self.assertContains(response, 'Import field map')
        self.assertContains(response, 'name="file"')
        self.assertContains(response, '<option value="Somerford" selected')
        response = self.client.get(reverse('location_list') + '?tab=map&site=Somerford')
        self.assertContains(response, self.url)

    def test_flag_off_redirects(self):
        with self.settings(LOCATION_MAPS_ENABLED=False):
            response = self.client.get(self.url)
        self.assertRedirects(response, reverse('location_list'))

    def test_view_only_role_is_denied(self):
        self.client.force_login(make_user_with_access('viewer', locations='view'))
        response = self.client.get(self.url)
        self.assertNotEqual(response.status_code, 200)

    def test_wrong_extension_and_too_big(self):
        response = self.client.post(self.url, {'site': 'Somerford', 'file': upload('landapp_bng.geojson', 'plan.kml')})
        self.assertContains(response, 'Upload a GeoJSON file')
        big = SimpleUploadedFile('big.geojson', b'{' + b' ' * (10 * 1024 * 1024 + 1) + b'}')
        response = self.client.post(self.url, {'site': 'Somerford', 'file': big})
        self.assertContains(response, 'over 10MB')

    def test_projected_file_is_rejected_with_the_reason(self):
        response = self.client.post(self.url, {'site': 'Somerford', 'file': upload('projected_other.geojson')})
        self.assertContains(response, 'projected coordinate system')
        self.assertNotIn('boundary_import', self.client.session)

    def test_good_file_goes_to_matching(self):
        response = self.client.post(self.url, {'site': 'Somerford', 'file': upload('landapp_bng.geojson')})
        self.assertRedirects(response, reverse('boundary_import_match'))
        data = self.client.session['boundary_import']
        self.assertEqual(data['site'], 'Somerford')
        self.assertEqual(len(data['shapes']), 4)
        self.assertTrue(data['converted'])


@override_settings(LOCATION_MAPS_ENABLED=True)
class MatchTests(TestCase):

    def setUp(self):
        self.user = make_admin()
        self.client.force_login(self.user)
        self.report = parse_geojson((FIXTURES / 'landapp_bng.geojson').read_bytes())
        first = self.report.shapes[0]
        self.inside = Location.objects.create(
            name='Grain store field', site='Somerford', capacity=10,
            latitude=Decimal(str(first.anchor[0])), longitude=Decimal(str(first.anchor[1])),
        )
        self.other = Location.objects.create(name='Far pen', site='Somerford')
        Location.objects.create(name='Elsewhere', site='Colgate')
        self.url = reverse('boundary_import_match')

    def start(self, name='landapp_bng.geojson'):
        self.client.post(reverse('boundary_import_upload'), {'site': 'Somerford', 'file': upload(name)})

    def test_without_an_upload_it_sends_you_to_step_one(self):
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse('boundary_import_upload'))

    def test_matching_page(self):
        self.start()
        response = self.client.get(self.url)
        self.assertContains(response, 'data-testid="shape-row"', count=4)
        self.assertContains(response, 'British National Grid')
        # The spatial suggestion is pre-selected and marked strong.
        self.assertContains(response, f'<option value="{self.inside.pk}" selected>Grain store field — suggested</option>')
        self.assertContains(response, "A location's pin sits inside this shape.")
        # Only this site's locations are offered.
        self.assertNotContains(response, 'Elsewhere')
        # Preview map with numbered badges, drawn by the shared partial.
        self.assertContains(response, 'data-testid="location-map"')
        self.assertContains(response, 'data-map-badge="shape-1"')
        self.assertContains(response, 'id="map-data-somerford-full"')
        payload = json.loads(response.context['map_payload'] and json.dumps(response.context['map_payload']))
        self.assertEqual([loc['kind'] for loc in payload['locations']], ['polygon'] * 4)
        self.assertContains(response, ' ha ')

    def test_invalid_shapes_are_listed_and_skipped(self):
        self.start('self_intersecting.geojson')
        response = self.client.get(self.url)
        self.assertContains(response, 'data-testid="import-invalid"')
        self.assertContains(response, 'Self-intersection')
        self.assertNotContains(response, 'name="shape_1"')
        self.assertContains(response, 'name="shape_2"')

    def test_commit_writes_matches_and_new_locations(self):
        self.start()
        response = self.client.post(self.url, {
            'shape_1': str(self.inside.pk),
            'shape_2': str(self.other.pk),
            'shape_3': 'new', 'new_name_3': 'Long Acre',
            'shape_4': 'skip',
        })
        self.assertRedirects(response, reverse('location_list') + '?tab=map&site=Somerford')
        self.inside.refresh_from_db()
        self.other.refresh_from_db()
        self.assertEqual(self.inside.boundary_source, 'landapp')
        self.assertEqual(self.inside.boundary['type'], 'Polygon')
        self.assertTrue(self.other.has_coordinates)            # gained a point from the shape
        self.assertIsNotNone(self.other.boundary)
        created = Location.objects.get(name='Long Acre')
        self.assertEqual((created.site, created.usage), ('Somerford', 'other'))
        self.assertIsNotNone(created.boundary)
        self.assertEqual(Location.objects.filter(site='Somerford').count(), 3)
        self.assertNotIn('boundary_import', self.client.session)
        self.assertEqual(LocationBoundaryHistory.objects.count(), 0)

    def test_same_location_twice_is_refused(self):
        self.start()
        response = self.client.post(self.url, {'shape_1': str(self.inside.pk), 'shape_2': str(self.inside.pk)})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'A location can take one boundary')
        self.inside.refresh_from_db()
        self.assertIsNone(self.inside.boundary)

    def test_nothing_chosen_is_refused(self):
        self.start()
        response = self.client.post(self.url, {'shape_1': 'skip', 'shape_2': 'skip', 'shape_3': 'skip', 'shape_4': 'skip'})
        self.assertContains(response, 'Nothing is matched')

    def test_overwrite_needs_confirmation_and_keeps_history(self):
        old = {'type': 'Polygon', 'coordinates': [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
        self.inside.boundary = old
        self.inside.boundary_source = 'manual'
        self.inside.save()
        self.start()
        response = self.client.get(self.url)
        self.assertContains(response, 'Grain store field (has a boundary)')
        response = self.client.post(self.url, {'shape_1': str(self.inside.pk)})
        self.assertContains(response, 'already have a boundary')
        self.inside.refresh_from_db()
        self.assertEqual(self.inside.boundary, old)
        response = self.client.post(self.url, {'shape_1': str(self.inside.pk), 'confirm_overwrite': '1'}, follow=True)
        self.assertContains(response, '1 previous boundary kept in the history')
        self.inside.refresh_from_db()
        self.assertNotEqual(self.inside.boundary, old)
        history = LocationBoundaryHistory.objects.get()
        self.assertEqual((history.boundary, history.source, history.replaced_by), (old, 'manual', self.user))

    def test_commit_is_atomic(self):
        from unittest import mock
        self.start()
        with mock.patch('core.views.boundary_import.apply_boundary', side_effect=[None, ValueError('boom')]):
            with self.assertRaises(ValueError):
                self.client.post(self.url, {'shape_1': str(self.inside.pk), 'shape_2': str(self.other.pk)})
        self.inside.refresh_from_db()
        self.assertIsNone(self.inside.boundary)
        self.assertIn('boundary_import', self.client.session)

    def test_cancel_clears_the_session(self):
        self.start()
        response = self.client.post(self.url, {'cancel': '1'})
        self.assertRedirects(response, reverse('location_list') + '?tab=map&site=Somerford')
        self.assertNotIn('boundary_import', self.client.session)

    def test_real_export_end_to_end_with_a_second_file(self):
        """The other real export (18 shapes, ponds and woods included)."""
        self.start('bng_undeclared.geojson')
        response = self.client.get(self.url)
        self.assertContains(response, 'data-testid="shape-row"', count=4)
        post = {f'shape_{i}': 'new' for i in range(1, 5)}
        response = self.client.post(self.url, post, follow=True)
        self.assertContains(response, '4 boundaries imported, 4 new locations created')
        self.assertEqual(Location.objects.filter(site='Somerford', boundary__isnull=False).count(), 4)
