"""Phase 4a of the location mapping plan: the boundary parser.

Each validation rule in core.boundary_import has its own fixture in
core/tests/fixtures/geo/. The real Land App / RPA export is in British
National Grid, so conversion (core.bng) is tested against known points.
"""

import json
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from shapely.geometry import Point, shape

from core import bng
from core.boundary_import import (
    BoundaryImportError, ImportedShape, anchor_for, apply_boundary, name_similarity,
    normalise_name, parse_geojson, shape_name, suggest_matches,
)
from core.models import BoundarySource, Location, LocationBoundaryHistory
from django.utils import timezone

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'geo'


def fixture(name):
    return (FIXTURES / name).read_bytes()


class BngConversionTests(TestCase):

    def test_known_points(self):
        # Ordnance Survey worked example (Caister water tower) and two sites.
        for e, n, lat, lng in [
            (651409.903, 313177.270, 52.657979, 1.716052),
            (395616.4, 183240.5, 51.548039, -2.064611),
            (530000, 180000, 51.503991, -0.128354),
        ]:
            got_lat, got_lng = bng.bng_to_wgs84(e, n)
            self.assertAlmostEqual(got_lat, lat, places=5)
            self.assertAlmostEqual(got_lng, lng, places=5)

    def test_position_order_is_geojson(self):
        lng, lat = bng.bng_position_to_lnglat([395616.4, 183240.5])
        self.assertAlmostEqual(lat, 51.548039, places=5)
        self.assertAlmostEqual(lng, -2.064611, places=5)

    def test_looks_like_bng(self):
        self.assertTrue(bng.looks_like_bng(395616.4, 183240.5))
        self.assertFalse(bng.looks_like_bng(-2.06, 51.55))
        self.assertFalse(bng.looks_like_bng(500000, 5700000))


class ParseGeojsonTests(TestCase):

    def test_real_export_in_bng_is_converted(self):
        report = parse_geojson(fixture('landapp_bng.geojson'))
        self.assertTrue(report.converted_from_bng)
        self.assertEqual(len(report.shapes), 4)
        self.assertEqual(report.discarded, 0)
        first = report.shapes[0]
        self.assertEqual(first.name, 'ST9583 7616')
        self.assertEqual(first.subtitle, 'Permanent Grassland')
        lat, lng = first.anchor
        self.assertTrue(51.5 < lat < 51.6 and -2.1 < lng < -2.0, first.anchor)
        for x, y in shape(first.geometry).exterior.coords:
            self.assertTrue(-180 <= x <= 180 and -90 <= y <= 90)
        self.assertGreater(first.hectares, 0.5)
        self.assertLess(first.hectares, 50)
        self.assertIn('British National Grid', report.notes[0])

    def test_bng_without_a_crs_member_is_detected_by_range(self):
        report = parse_geojson(fixture('bng_undeclared.geojson'))
        self.assertTrue(report.converted_from_bng)
        self.assertEqual(len(report.shapes), 4)

    def test_wgs84_export(self):
        report = parse_geojson(fixture('landapp_wgs84.geojson'))
        self.assertFalse(report.converted_from_bng)
        self.assertEqual([s.name for s in report.shapes][:2], ['Field 7616', 'Field 0986'])
        self.assertEqual(report.notes, [])

    def test_other_projected_system_is_rejected(self):
        with self.assertRaises(BoundaryImportError) as cm:
            parse_geojson(fixture('projected_other.geojson'))
        self.assertIn('projected coordinate system', str(cm.exception))

    def test_points_and_lines_are_discarded_and_counted(self):
        report = parse_geojson(fixture('mixed_types.geojson'))
        self.assertEqual([s.name for s in report.shapes], ['Top field', 'Bottom field'])
        self.assertEqual(report.discarded, 2)
        self.assertIn('2 features were not a field boundary', report.notes[0])

    def test_winding_order_is_normalised(self):
        report = parse_geojson(fixture('mixed_types.geojson'))
        for shp in report.shapes:
            self.assertTrue(shape(shp.geometry).exterior.is_ccw, shp.name)

    def test_self_intersection_is_reported_not_imported(self):
        report = parse_geojson(fixture('self_intersecting.geojson'))
        bad = report.shapes[0]
        self.assertEqual(bad.name, 'Bowtie')
        self.assertIn('Self-intersection', bad.invalid)
        self.assertFalse(bad.importable)
        self.assertEqual([s.name for s in report.importable], ['Good square'])
        self.assertEqual([s.name for s in report.invalid], ['Bowtie'])

    def test_dense_polygon_is_simplified(self):
        report = parse_geojson(fixture('dense_5000.geojson'))
        shp = report.shapes[0]
        self.assertTrue(shp.simplified)
        self.assertLess(shp.vertex_count, 5001)
        self.assertEqual(shp.name, 'Round field')

    def test_multipolygon_with_hole(self):
        report = parse_geojson(fixture('multipolygon_hole.geojson'))
        shp = report.shapes[0]
        self.assertEqual(shp.geometry['type'], 'MultiPolygon')
        self.assertEqual(shp.name, 'Two parts')
        geom = shape(shp.geometry)
        self.assertTrue(geom.is_valid)
        self.assertEqual(len(geom.geoms[0].interiors), 1)
        self.assertTrue(geom.contains(Point(shp.anchor[1], shp.anchor[0])))

    def test_empty_collection_is_explained(self):
        with self.assertRaises(BoundaryImportError) as cm:
            parse_geojson(fixture('empty.geojson'))
        self.assertIn('no features', str(cm.exception))

    def test_only_markers_is_explained(self):
        doc = {'type': 'FeatureCollection', 'features': [
            {'type': 'Feature', 'properties': {}, 'geometry': {'type': 'Point', 'coordinates': [-2, 51]}},
        ]}
        with self.assertRaises(BoundaryImportError) as cm:
            parse_geojson(json.dumps(doc))
        self.assertIn('only points, lines', str(cm.exception))

    def test_bad_json_and_bad_geojson(self):
        with self.assertRaises(BoundaryImportError) as cm:
            parse_geojson(b'not json')
        self.assertIn('valid JSON', str(cm.exception))
        with self.assertRaises(BoundaryImportError) as cm:
            parse_geojson(b'\xff\xfe\x00')
        self.assertIn("isn't text", str(cm.exception))
        with self.assertRaises(BoundaryImportError) as cm:
            parse_geojson(json.dumps({'type': 'Polygon', 'coordinates': []}))
        self.assertIn('FeatureCollection or Feature', str(cm.exception))
        with self.assertRaises(BoundaryImportError):
            parse_geojson(json.dumps([1, 2]))

    def test_single_feature_is_accepted(self):
        doc = json.loads(fixture('concave_l.geojson'))['features'][0]
        report = parse_geojson(json.dumps(doc))
        self.assertEqual(len(report.shapes), 1)

    def test_concave_anchor_sits_inside_the_shape(self):
        report = parse_geojson(fixture('concave_l.geojson'))
        shp = report.shapes[0]
        geom = shape(shp.geometry)
        self.assertTrue(geom.contains(Point(shp.anchor[1], shp.anchor[0])))
        # …where the centroid would not.
        self.assertFalse(geom.contains(geom.centroid))
        self.assertEqual(anchor_for(shp.geometry), shp.anchor)

    def test_shape_round_trips_through_a_dict(self):
        shp = parse_geojson(fixture('concave_l.geojson')).shapes[0]
        again = ImportedShape.from_dict(json.loads(json.dumps(shp.as_dict())))
        self.assertEqual(again, shp)

    def test_duplicate_names_are_told_apart(self):
        doc = json.loads(fixture('mixed_types.geojson'))
        for f in doc['features'][:2]:
            f['properties'] = {'sheetId': 'ST9582', 'parcelId': '2813', 'description': 'Pond' if f is doc['features'][0] else ''}
        report = parse_geojson(json.dumps(doc))
        self.assertEqual([s.name for s in report.shapes], ['ST9582 2813 (Pond)', 'ST9582 2813 (2)'])

    def test_shape_name_fallbacks(self):
        self.assertEqual(shape_name({'name': 'Top'}, 1), 'Top')
        self.assertEqual(shape_name({'label': 'L'}, 1), 'L')
        self.assertEqual(shape_name({'sheetId': 'ST9583', 'parcelId': '7616'}, 1), 'ST9583 7616')
        self.assertEqual(shape_name({'colour': 'red'}, 7), 'Shape 7')
        self.assertEqual(shape_name({'name': '   '}, 2), 'Shape 2')


class MatchingTests(TestCase):

    def test_normalise_and_similarity(self):
        self.assertEqual(normalise_name("Jones's mid west - one up from grain store"), ['jones', 's', 'mid', 'west', 'one', 'up', 'from', 'grain', 'store'])
        self.assertEqual(normalise_name('The Top Field'), ['top'])
        self.assertGreaterEqual(name_similarity('Top Field', 'Top'), 0.6)
        self.assertGreaterEqual(name_similarity('Grain store field', 'Grain Store'), 0.6)
        self.assertLess(name_similarity('ST9583 7616', 'Grain store field'), 0.6)
        # Two parcel codes look alike character by character; that is not a match.
        self.assertEqual(name_similarity('SO9820 8294', 'ST9482 4843'), 0.0)
        self.assertGreaterEqual(name_similarity('Whitakers', 'Whitaker'), 0.8)
        self.assertEqual(name_similarity('', 'x'), 0.0)

    def test_spatial_beats_name_and_each_location_is_used_once(self):
        report = parse_geojson(fixture('mixed_types.geojson'))
        top, bottom = report.shapes
        inside_top = Point(top.anchor[1], top.anchor[0])
        # A location whose point sits in "Top field" but is named like "Bottom field".
        pinned = Location.objects.create(
            name='Bottom field', site='S',
            latitude=Decimal(str(inside_top.y)), longitude=Decimal(str(inside_top.x)),
        )
        by_name = Location.objects.create(name='Bottom Field (old)', site='S')
        Location.objects.create(name='Unrelated', site='S')
        suggestions = suggest_matches(report.shapes, list(Location.objects.active()))
        self.assertEqual(suggestions[top.index], {'location': pinned, 'strength': 'strong'})
        self.assertEqual(suggestions[bottom.index], {'location': by_name, 'strength': 'weak'})

    def test_no_suggestion_when_nothing_fits(self):
        report = parse_geojson(fixture('landapp_bng.geojson'))
        Location.objects.create(name='Grain store field', site='S')
        self.assertEqual(suggest_matches(report.shapes, list(Location.objects.active())), {})

    def test_invalid_shapes_get_no_suggestion(self):
        report = parse_geojson(fixture('self_intersecting.geojson'))
        Location.objects.create(name='Bowtie', site='S')
        suggestions = suggest_matches(report.shapes, list(Location.objects.active()))
        self.assertNotIn(report.shapes[0].index, suggestions)


class ApplyBoundaryTests(TestCase):

    def test_sets_boundary_and_missing_point(self):
        shp = parse_geojson(fixture('concave_l.geojson')).shapes[0]
        loc = Location.objects.create(name='L', site='S')
        now = timezone.now()
        previous = apply_boundary(loc, shp, source=BoundarySource.LANDAPP, now=now)
        self.assertIsNone(previous)
        loc.refresh_from_db()
        self.assertEqual(loc.boundary, shp.geometry)
        self.assertEqual(loc.boundary_source, 'landapp')
        self.assertEqual(loc.boundary_updated_at, now)
        self.assertEqual((float(loc.latitude), float(loc.longitude)), shp.anchor)
        self.assertEqual(LocationBoundaryHistory.objects.count(), 0)

    def test_keeps_an_existing_point_and_records_the_old_boundary(self):
        shp = parse_geojson(fixture('concave_l.geojson')).shapes[0]
        old = {'type': 'Polygon', 'coordinates': [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
        loc = Location.objects.create(
            name='L', site='S', latitude=Decimal('51.5'), longitude=Decimal('-2.1'),
            boundary=old, boundary_source='manual',
        )
        previous = apply_boundary(loc, shp, source=BoundarySource.LANDAPP, now=timezone.now())
        self.assertEqual(previous, old)
        loc.refresh_from_db()
        self.assertEqual(loc.latitude, Decimal('51.5'))
        history = LocationBoundaryHistory.objects.get()
        self.assertEqual(history.boundary, old)
        self.assertEqual(history.source, 'manual')
        self.assertEqual(history.location, loc)


class ImportCommandTests(TestCase):

    def setUp(self):
        self.report = parse_geojson(fixture('landapp_bng.geojson'))
        first = self.report.shapes[0]
        self.inside = Location.objects.create(
            name='Grain store field', site='Somerford',
            latitude=Decimal(str(first.anchor[0])), longitude=Decimal(str(first.anchor[1])),
        )
        self.other = Location.objects.create(name='Far pen', site='Somerford')
        Location.objects.create(name='Elsewhere', site='Colgate')

    def run_command(self, *args):
        out = StringIO()
        call_command('import_boundaries', str(FIXTURES / 'landapp_bng.geojson'), *args, stdout=out)
        return out.getvalue()

    def test_dry_run_reports_and_writes_nothing(self):
        out = self.run_command('--site', 'Somerford')
        self.assertIn('British National Grid', out)
        self.assertIn('4 shape(s) in the file, 2 active location(s) on Somerford', out)
        self.assertIn('→ Grain store field  [strong match]', out)
        self.assertIn('no match', out)
        self.assertIn('Dry run', out)
        self.inside.refresh_from_db()
        self.assertIsNone(self.inside.boundary)

    def test_write_saves_spatial_matches_only(self):
        self.run_command('--site', 'Somerford', '--write')
        self.inside.refresh_from_db()
        self.other.refresh_from_db()
        self.assertEqual(self.inside.boundary_source, 'landapp')
        self.assertEqual(self.inside.boundary['type'], 'Polygon')
        self.assertIsNone(self.other.boundary)

    def test_assign_and_create(self):
        out = self.run_command('--site', 'Somerford', '--write', '--create', '--assign', f'2={self.other.pk}')
        self.assertIn(f'→ {self.other.name}  [assigned]', out)
        self.assertIn('NEW location', out)
        self.other.refresh_from_db()
        self.assertIsNotNone(self.other.boundary)
        self.assertTrue(self.other.has_coordinates)
        created = Location.objects.filter(site='Somerford').exclude(pk__in=[self.inside.pk, self.other.pk])
        self.assertEqual(created.count(), 2)
        self.assertTrue(all(loc.boundary and loc.has_coordinates for loc in created))
        self.assertEqual(created.first().usage, 'other')

    def test_existing_boundary_is_kept_without_overwrite(self):
        self.inside.boundary = {'type': 'Polygon', 'coordinates': [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
        self.inside.save()
        out = self.run_command('--site', 'Somerford', '--write')
        self.assertIn('already has a boundary', out)
        self.inside.refresh_from_db()
        self.assertEqual(self.inside.boundary['coordinates'][0][0], [0, 0])
        self.run_command('--site', 'Somerford', '--write', '--overwrite')
        self.inside.refresh_from_db()
        self.assertNotEqual(self.inside.boundary['coordinates'][0][0], [0, 0])
        self.assertEqual(LocationBoundaryHistory.objects.filter(location=self.inside).count(), 1)

    def test_bad_arguments(self):
        with self.assertRaises(CommandError):
            self.run_command('--create')
        with self.assertRaises(CommandError):
            self.run_command('--assign', 'nonsense')
        with self.assertRaises(CommandError):
            self.run_command('--assign', '1=999999')
        with self.assertRaises(CommandError):
            call_command('import_boundaries', '/no/such/file.geojson')

    def test_rejected_file_is_a_command_error(self):
        with self.assertRaises(CommandError) as cm:
            call_command('import_boundaries', str(FIXTURES / 'projected_other.geojson'))
        self.assertIn('projected coordinate system', str(cm.exception))

    def test_write_is_atomic(self):
        # A location whose stored boundary must be replaced sits inside shape 1;
        # make the second planned write fail and check the first was rolled back.
        from unittest import mock
        from core.management.commands import import_boundaries as cmd
        real = cmd.apply_boundary
        calls = {'n': 0}

        def flaky(*args, **kwargs):
            calls['n'] += 1
            if calls['n'] == 2:
                from django.core.exceptions import ValidationError
                raise ValidationError('boom')
            return real(*args, **kwargs)

        with mock.patch.object(cmd, 'apply_boundary', side_effect=flaky):
            with self.assertRaises(CommandError):
                self.run_command('--site', 'Somerford', '--write', '--assign', f'2={self.other.pk}')
        self.inside.refresh_from_db()
        self.assertIsNone(self.inside.boundary)
