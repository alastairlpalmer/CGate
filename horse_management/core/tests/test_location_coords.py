"""Phase 1 of the location mapping plan: coordinates.

Covers the Google Maps link parser (core.geo), the coordinate rules in
``clean()`` and at the database, the backfill command (dry run, write,
idempotent), the three ways into the edit form inside the pop-up sheet,
the Edit site form, and the far-from-site warning.
"""

from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from core import geo
from core.forms import LocationForm, SiteSettingsForm
from core.models import DashboardPreference, Location, SiteSettings
from core.roles_testutils import make_admin

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}


class GeoParserTests(TestCase):

    def test_at_path_form(self):
        url = 'https://www.google.com/maps/place/Somerford/@51.548038,-2.064611,17z/data=!3m1!4b1'
        self.assertEqual(geo.parse_maps_url(url), (Decimal('51.548038'), Decimal('-2.064611')))

    def test_query_q_form(self):
        self.assertEqual(
            geo.parse_maps_url('https://maps.google.com/?q=51.5,-2.1'),
            (Decimal('51.5'), Decimal('-2.1')),
        )

    def test_query_param_form(self):
        url = 'https://www.google.com/maps/search/?api=1&query=51.5074%2C-0.1278'
        self.assertEqual(geo.parse_maps_url(url), (Decimal('51.5074'), Decimal('-0.1278')))

    def test_data_3d4d_form(self):
        url = 'https://www.google.com/maps/place/x/data=!4m5!3m4!1s0x0:0x0!8m2!3d51.885515!4d-2.020603'
        self.assertEqual(geo.parse_maps_url(url), (Decimal('51.885515'), Decimal('-2.020603')))

    def test_at_wins_over_data(self):
        url = 'https://www.google.com/maps/place/x/@51.1,-2.1,15z/data=!3d51.2!4d-2.2'
        self.assertEqual(geo.parse_maps_url(url), (Decimal('51.1'), Decimal('-2.1')))

    def test_malformed_and_place_only(self):
        self.assertIsNone(geo.parse_maps_url(''))
        self.assertIsNone(geo.parse_maps_url('not a url'))
        self.assertIsNone(geo.parse_maps_url('https://www.google.com/maps/place/Somerford+Farm/'))
        self.assertIsNone(geo.parse_maps_url('https://maps.google.com/?q=Somerford+Farm'))

    def test_extract_url_from_free_text(self):
        text = 'next to grain store https://maps.app.goo.gl/AbC123. Gate on the left'
        self.assertEqual(geo.extract_url(text), 'https://maps.app.goo.gl/AbC123')
        self.assertIsNone(geo.extract_url('no link here'))
        self.assertIsNone(geo.extract_url(''))

    def test_short_link_detection(self):
        self.assertTrue(geo.is_short_link('https://maps.app.goo.gl/AbC'))
        self.assertTrue(geo.is_short_link('https://goo.gl/maps/AbC'))
        self.assertFalse(geo.is_short_link('https://www.google.com/maps/@51,-2,15z'))

    def test_parse_coords_text(self):
        self.assertEqual(geo.parse_coords_text('52.1234, -1.2345'), (Decimal('52.1234'), Decimal('-1.2345')))
        self.assertEqual(geo.parse_coords_text('52.1234 -1.2345'), (Decimal('52.1234'), Decimal('-1.2345')))
        self.assertEqual(geo.parse_coords_text(' 52,-1 '), (Decimal('52'), Decimal('-1')))
        self.assertIsNone(geo.parse_coords_text('52.1234'))
        self.assertIsNone(geo.parse_coords_text('north field'))
        self.assertIsNone(geo.parse_coords_text(''))

    def test_coords_from_link_without_resolving(self):
        coords, url = geo.coords_from_link(
            'gate https://www.google.com/maps/@51.5,-2.1,15z', resolve=False,
        )
        self.assertEqual(coords, (Decimal('51.5'), Decimal('-2.1')))
        self.assertIn('@51.5', url)
        self.assertEqual(geo.coords_from_link('nothing', resolve=False), (None, None))

    def test_haversine(self):
        # London to Paris, about 344 km.
        d = geo.haversine_m(51.5074, -0.1278, 48.8566, 2.3522)
        self.assertAlmostEqual(d / 1000, 343.5, delta=1.5)
        self.assertEqual(geo.haversine_m(10, 10, 10, 10), 0)


class CoordinateValidationTests(TestCase):

    def test_clean_rejects_out_of_range(self):
        loc = Location(name='A', site='S', latitude=Decimal('91'), longitude=Decimal('0'))
        with self.assertRaises(ValidationError) as cm:
            loc.full_clean()
        self.assertIn('latitude', cm.exception.message_dict)
        loc = Location(name='A', site='S', latitude=Decimal('51'), longitude=Decimal('181'))
        with self.assertRaises(ValidationError) as cm:
            loc.full_clean()
        self.assertIn('longitude', cm.exception.message_dict)

    def test_clean_rejects_null_island_and_half_pairs(self):
        with self.assertRaises(ValidationError):
            Location(name='A', site='S', latitude=Decimal('0'), longitude=Decimal('0')).full_clean()
        with self.assertRaises(ValidationError) as cm:
            Location(name='A', site='S', latitude=Decimal('51')).full_clean()
        self.assertIn('longitude', cm.exception.message_dict)
        # Both blank is fine.
        Location(name='A', site='S').full_clean()
        SiteSettings(site='S').full_clean()
        with self.assertRaises(ValidationError):
            SiteSettings(site='S', latitude=Decimal('0'), longitude=Decimal('0')).full_clean()

    def test_database_constraints_hold_without_clean(self):
        bad = [
            dict(latitude=Decimal('95'), longitude=Decimal('0')),
            dict(latitude=Decimal('0'), longitude=Decimal('0')),
            dict(latitude=Decimal('51'), longitude=None),
        ]
        for values in bad:
            with self.subTest(values=values), transaction.atomic():
                with self.assertRaises(IntegrityError):
                    Location.objects.create(name='A', site='S', **values)
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                SiteSettings.objects.create(site='S', latitude=Decimal('0'), longitude=Decimal('0'))

    def test_helpers(self):
        loc = Location(name='A', site='S', latitude=Decimal('51.5'), longitude=Decimal('-2.1'))
        self.assertTrue(loc.has_coordinates)
        self.assertEqual(loc.maps_url, 'https://www.google.com/maps?q=51.5,-2.1')
        self.assertFalse(Location(name='B', site='S').has_coordinates)
        self.assertEqual(Location(name='B', site='S').maps_url, '')

    def test_site_distance_warning(self):
        site = SiteSettings(site='S', latitude=Decimal('51.5'), longitude=Decimal('-2.1'))
        near = Location(name='Near', site='S', latitude=Decimal('51.51'), longitude=Decimal('-2.11'))
        far = Location(name='Far', site='S', latitude=Decimal('-2.1'), longitude=Decimal('51.5'))
        self.assertIsNone(geo.site_distance_warning(near, site))
        self.assertIn('right way round', geo.site_distance_warning(far, site))
        self.assertIsNone(geo.site_distance_warning(far, None))
        self.assertIsNone(geo.site_distance_warning(Location(name='x', site='S'), site))


class BackfillCommandTests(TestCase):

    def setUp(self):
        self.full = Location.objects.create(
            name='Full link', site='S',
            description='by the barn https://www.google.com/maps/place/x/@51.548038,-2.064611,17z',
        )
        self.short = Location.objects.create(
            name='Short link', site='S', description='https://maps.app.goo.gl/AbC123',
        )
        self.none = Location.objects.create(name='No link', site='S', description='top field')
        self.place = Location.objects.create(
            name='Place only', site='S', description='https://www.google.com/maps/place/Somerford/',
        )
        self.done = Location.objects.create(
            name='Already', site='S', description='https://www.google.com/maps/@50,-1,15z',
            latitude=Decimal('50.5'), longitude=Decimal('-1.5'),
        )
        self.archived = Location.objects.create(
            name='Gone', site='S', is_archived=True,
            description='https://www.google.com/maps/@50,-1,15z',
        )

    def run_command(self, *args):
        out = StringIO()
        with mock.patch('core.management.commands.backfill_location_coords.resolve_short_link') as resolve, \
                mock.patch('core.management.commands.backfill_location_coords.time.sleep'):
            resolve.return_value = 'https://www.google.com/maps/@51.885515,-2.020603,15z'
            call_command('backfill_location_coords', *args, stdout=out)
        return out.getvalue()

    def test_dry_run_reports_without_writing(self):
        out = self.run_command()
        self.assertIn('would write', out)
        self.assertIn('2 resolved, 2 unresolved, 0 rejected, 1 skipped', out)
        self.assertIn('Dry run', out)
        self.full.refresh_from_db()
        self.assertIsNone(self.full.latitude)

    def test_write_saves_and_second_run_skips(self):
        self.run_command('--write')
        self.full.refresh_from_db()
        self.short.refresh_from_db()
        self.assertEqual((self.full.latitude, self.full.longitude), (Decimal('51.548038'), Decimal('-2.064611')))
        self.assertEqual((self.short.latitude, self.short.longitude), (Decimal('51.885515'), Decimal('-2.020603')))
        self.none.refresh_from_db()
        self.assertIsNone(self.none.latitude)
        # Description stays as the audit trail.
        self.assertIn('goo.gl', self.short.description)
        out = self.run_command('--write')
        self.assertIn('0 resolved, 2 unresolved, 0 rejected, 3 skipped', out)

    def test_archived_locations_are_ignored(self):
        self.run_command('--write')
        self.archived.refresh_from_db()
        self.assertIsNone(self.archived.latitude)

    def test_unresolved_reasons_and_rejects(self):
        Location.objects.create(
            name='Null island', site='S', description='https://www.google.com/maps/@0,0,15z',
        )
        out = self.run_command()
        self.assertIn('No link', out)
        self.assertIn('no link in description', out)
        self.assertIn('no coordinates in', out)
        self.assertIn('Null island', out)
        self.assertIn('1 rejected', out)

    def test_network_failure_is_unresolved_not_fatal(self):
        out = StringIO()
        with mock.patch('core.management.commands.backfill_location_coords.resolve_short_link', side_effect=OSError('boom')), \
                mock.patch('core.management.commands.backfill_location_coords.time.sleep'):
            call_command('backfill_location_coords', stdout=out)
        self.assertIn('could not follow', out.getvalue())

    def test_no_resolve_flag_leaves_short_links(self):
        out = StringIO()
        with mock.patch('core.management.commands.backfill_location_coords.resolve_short_link') as resolve:
            call_command('backfill_location_coords', '--no-resolve', stdout=out)
        resolve.assert_not_called()
        self.assertIn('short link not followed', out.getvalue())


def _form_data(**overrides):
    data = {
        'name': 'Flat Whitakers', 'site': 'Somerford', 'usage': 'horses',
        'description': '', 'capacity': '', 'latitude': '', 'longitude': '',
        'coords_text': '', 'maps_link': '',
    }
    data.update(overrides)
    return data


class LocationFormCoordinateTests(TestCase):

    def test_hidden_pair_saves(self):
        form = LocationForm(data=_form_data(latitude='51.548038', longitude='-2.064611'))
        self.assertTrue(form.is_valid(), form.errors)
        loc = form.save()
        self.assertEqual((loc.latitude, loc.longitude), (Decimal('51.548038'), Decimal('-2.064611')))

    def test_pasted_text_wins_over_hidden_pair(self):
        form = LocationForm(data=_form_data(latitude='1', longitude='1', coords_text='52.1234, -1.2345'))
        self.assertTrue(form.is_valid(), form.errors)
        loc = form.save()
        self.assertEqual((loc.latitude, loc.longitude), (Decimal('52.1234'), Decimal('-1.2345')))

    def test_maps_link_is_parsed_server_side(self):
        with mock.patch('core.forms.coords_from_link', return_value=((Decimal('51.5'), Decimal('-2.1')), 'u')):
            form = LocationForm(data=_form_data(maps_link='https://maps.app.goo.gl/AbC'))
            self.assertTrue(form.is_valid(), form.errors)
        loc = form.save()
        self.assertEqual((loc.latitude, loc.longitude), (Decimal('51.5'), Decimal('-2.1')))

    def test_maps_link_without_coordinates_errors(self):
        with mock.patch('core.forms.coords_from_link', return_value=(None, 'u')):
            form = LocationForm(data=_form_data(maps_link='https://www.google.com/maps/place/x/'))
            self.assertFalse(form.is_valid())
        self.assertIn('No coordinates', form.errors['maps_link'][0])

    def test_maps_link_network_failure_errors(self):
        with mock.patch('core.forms.coords_from_link', side_effect=OSError('down')):
            form = LocationForm(data=_form_data(maps_link='https://maps.app.goo.gl/AbC'))
            self.assertFalse(form.is_valid())
        self.assertIn("couldn't be opened", form.errors['maps_link'][0])

    def test_bad_text_and_bad_values_land_on_the_visible_box(self):
        form = LocationForm(data=_form_data(coords_text='north field'))
        self.assertFalse(form.is_valid())
        self.assertIn('coords_text', form.errors)
        form = LocationForm(data=_form_data(coords_text='0, 0'))
        self.assertFalse(form.is_valid())
        self.assertIn('0, 0', form.errors['coords_text'][0])
        form = LocationForm(data=_form_data(latitude='95', longitude='1'))
        self.assertFalse(form.is_valid())
        self.assertIn('coords_text', form.errors)
        self.assertNotIn('__all__', form.errors)

    def test_clearing_removes_coordinates(self):
        loc = Location.objects.create(
            name='A', site='S', latitude=Decimal('51.5'), longitude=Decimal('-2.1'),
        )
        form = LocationForm(data=_form_data(name='A', site='S'), instance=loc)
        self.assertTrue(form.is_valid(), form.errors)
        loc = form.save()
        self.assertIsNone(loc.latitude)
        self.assertIsNone(loc.longitude)

    def test_pin_field_only_with_user_and_saved_row(self):
        self.assertNotIn('pin_to_dashboard', LocationForm().fields)
        loc = Location.objects.create(name='A', site='S')
        user = make_admin()
        form = LocationForm(instance=loc, user=user)
        self.assertIn('pin_to_dashboard', form.fields)
        self.assertNotIn('pin_to_dashboard', LocationForm(user=user).fields)


class LocationFormViewTests(TestCase):

    def setUp(self):
        self.user = make_admin()
        self.client.force_login(self.user)
        self.location = Location.objects.create(name='Grain store field', site='Somerford')

    def test_edit_form_in_the_sheet_carries_the_picker(self):
        response = self.client.get(reverse('location_update', args=[self.location.pk]), **POPUP)
        self.assertContains(response, 'data-testid="coord-picker"')
        self.assertContains(response, 'data-map')
        self.assertContains(response, 'name="latitude"', count=1)
        self.assertContains(response, 'name="coords_text"')
        self.assertContains(response, 'name="maps_link"')
        self.assertContains(response, 'coordPicker(52.5, -1.9, 6')

    def test_full_page_form_carries_the_picker_once(self):
        response = self.client.get(reverse('location_update', args=[self.location.pk]))
        self.assertContains(response, 'data-testid="coord-picker"', count=1)
        self.assertContains(response, 'name="latitude"', count=1)

    def test_picker_centres_on_the_site_or_a_neighbour(self):
        Location.objects.create(
            name='Neighbour', site='Somerford', latitude=Decimal('51.5'), longitude=Decimal('-2.1'),
        )
        response = self.client.get(reverse('location_update', args=[self.location.pk]), **POPUP)
        self.assertContains(response, 'coordPicker(51.5, -2.1, 15')
        SiteSettings.objects.create(site='Somerford', latitude=Decimal('51.6'), longitude=Decimal('-2.2'))
        response = self.client.get(reverse('location_update', args=[self.location.pk]), **POPUP)
        self.assertContains(response, 'coordPicker(51.6, -2.2, 15')

    def test_save_in_the_sheet_by_pasted_text(self):
        response = self.client.post(
            reverse('location_update', args=[self.location.pk]),
            _form_data(name='Grain store field', coords_text='51.548038 -2.064611'),
            **POPUP,
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response['HX-Trigger'], 'popup:saved')
        self.location.refresh_from_db()
        self.assertEqual(self.location.latitude, Decimal('51.548038'))

    def test_save_by_dragged_pin_and_by_link(self):
        self.client.post(
            reverse('location_update', args=[self.location.pk]),
            _form_data(name='Grain store field', latitude='51.1', longitude='-2.1'),
            **POPUP,
        )
        self.location.refresh_from_db()
        self.assertEqual(self.location.longitude, Decimal('-2.1'))
        with mock.patch('core.forms.coords_from_link', return_value=((Decimal('51.2'), Decimal('-2.2')), 'u')):
            self.client.post(
                reverse('location_update', args=[self.location.pk]),
                _form_data(name='Grain store field', maps_link='https://maps.app.goo.gl/x'),
                **POPUP,
            )
        self.location.refresh_from_db()
        self.assertEqual(self.location.latitude, Decimal('51.2'))

    def test_invalid_pair_rerenders_the_sheet_with_the_error(self):
        response = self.client.post(
            reverse('location_update', args=[self.location.pk]),
            _form_data(name='Grain store field', coords_text='95, 1'),
            **POPUP,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Latitude must be between')
        self.assertNotContains(response, 'Hidden field')

    def test_far_from_site_warns_but_saves(self):
        SiteSettings.objects.create(site='Somerford', latitude=Decimal('51.5'), longitude=Decimal('-2.1'))
        response = self.client.post(
            reverse('location_update', args=[self.location.pk]),
            _form_data(name='Grain store field', coords_text='-2.1, 51.5'),
            follow=True,
        )
        self.location.refresh_from_db()
        self.assertEqual(self.location.latitude, Decimal('-2.1'))
        self.assertContains(response, 'right way round')

    def test_create_with_coordinates(self):
        response = self.client.post(
            reverse('location_create'), _form_data(name='New pen', coords_text='51.5, -2.1'),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Location.objects.get(name='New pen').latitude, Decimal('51.5'))

    def test_pin_saves_to_the_users_preference(self):
        self.client.post(
            reverse('location_update', args=[self.location.pk]),
            _form_data(name='Grain store field', pin_to_dashboard='on'),
            **POPUP,
        )
        self.assertEqual(DashboardPreference.get_for(self.user).pinned_location, self.location)
        self.client.post(
            reverse('location_update', args=[self.location.pk]),
            _form_data(name='Grain store field'),
            **POPUP,
        )
        self.assertIsNone(DashboardPreference.get_for(self.user).pinned_location)

    def test_locations_page_shows_a_maps_link_only_with_coordinates(self):
        response = self.client.get(reverse('location_list'))
        self.assertNotContains(response, 'google.com/maps?q=')
        # No map on the list page: the Leaflet loader only fires for [data-map]
        # elements, and the head's loader script is the only mention.
        self.assertNotContains(response, 'data-testid="coord-picker"')
        self.location.latitude, self.location.longitude = Decimal('51.5'), Decimal('-2.1')
        self.location.save()
        response = self.client.get(reverse('location_list'))
        self.assertContains(response, 'https://www.google.com/maps?q=51.5,-2.1')

    def test_parse_link_endpoint(self):
        with mock.patch('core.views.locations.coords_from_link', return_value=((Decimal('51.5'), Decimal('-2.1')), 'full')):
            response = self.client.get(reverse('location_parse_link'), {'link': 'https://maps.app.goo.gl/x'})
        self.assertEqual(response.json(), {'ok': True, 'latitude': '51.5', 'longitude': '-2.1', 'url': 'full'})
        with mock.patch('core.views.locations.coords_from_link', return_value=(None, 'full')):
            response = self.client.get(reverse('location_parse_link'), {'link': 'https://x'})
        self.assertFalse(response.json()['ok'])
        self.assertEqual(self.client.get(reverse('location_parse_link')).status_code, 400)


class SiteSettingsViewTests(TestCase):

    def setUp(self):
        self.client.force_login(make_admin())
        Location.objects.create(name='A', site='Somerford')

    def test_edit_site_link_on_the_locations_page(self):
        response = self.client.get(reverse('location_list'))
        self.assertContains(response, 'Edit site')
        self.assertContains(response, reverse('site_settings') + '?site=Somerford')

    def test_get_renders_the_picker_in_the_sheet(self):
        response = self.client.get(reverse('site_settings') + '?site=Somerford', **POPUP)
        self.assertContains(response, 'data-testid="coord-picker"')
        self.assertContains(response, 'Site centre')
        self.assertContains(response, 'name="radius_m"')

    def test_post_saves_centre_and_radius(self):
        response = self.client.post(
            reverse('site_settings') + '?site=Somerford',
            {'latitude': '', 'longitude': '', 'radius_m': '2000', 'coords_text': '51.5, -2.1', 'maps_link': ''},
            **POPUP,
        )
        self.assertEqual(response.status_code, 204)
        settings = SiteSettings.objects.get(site='Somerford')
        self.assertEqual((settings.latitude, settings.longitude, settings.radius_m), (Decimal('51.5'), Decimal('-2.1'), 2000))
        # Editing again updates the same row.
        self.client.post(
            reverse('site_settings') + '?site=Somerford',
            {'latitude': '51.6', 'longitude': '-2.2', 'radius_m': '1500', 'coords_text': '', 'maps_link': ''},
        )
        self.assertEqual(SiteSettings.objects.count(), 1)
        self.assertEqual(SiteSettings.objects.get().latitude, Decimal('51.6'))

    def test_unknown_site_redirects(self):
        response = self.client.get(reverse('site_settings') + '?site=Nowhere')
        self.assertRedirects(response, reverse('location_list'))

    def test_form_validates_like_the_location_form(self):
        form = SiteSettingsForm(data={'latitude': '0', 'longitude': '0', 'radius_m': '100', 'coords_text': '', 'maps_link': ''})
        self.assertFalse(form.is_valid())
        self.assertIn('coords_text', form.errors)


@override_settings(LOCATION_MAPS_ENABLED=True)
class FeatureFlagContextTests(TestCase):

    def test_flag_reaches_templates(self):
        from core.context_processors import location_maps
        self.assertTrue(location_maps(None)['location_maps_enabled'])
        with self.settings(LOCATION_MAPS_ENABLED=False):
            self.assertFalse(location_maps(None)['location_maps_enabled'])
