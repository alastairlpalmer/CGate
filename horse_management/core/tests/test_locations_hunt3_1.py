"""Locations, imports and horse photos: third bug hunt (each test failed before the fix)."""
import json, os, shutil, tempfile
from datetime import date, timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, LocationUsagePeriod, Owner, Placement, RateType, HorsePhoto
from core.roles_testutils import make_admin, make_user_with_access
from core.services import PlacementService, LocationUsageService

SQ = [[-1.0, 51.0], [-0.999, 51.0], [-0.999, 51.001], [-1.0, 51.001], [-1.0, 51.0]]


def fc(features):
    return json.dumps({'type': 'FeatureCollection', 'features': features}).encode()


def feat(name, coords):
    return {'type': 'Feature', 'properties': {'name': name}, 'geometry': {'type': 'Polygon', 'coordinates': [coords]}}


class TempMedia:
    def setUp(self):
        self._media = tempfile.mkdtemp()
        o = override_settings(MEDIA_ROOT=self._media)
        o.enable(); self.addCleanup(o.disable)
        self.addCleanup(shutil.rmtree, self._media, True)
        super().setUp()


@override_settings(LOCATION_MAPS_ENABLED=True)
class BoundaryProbes(TempMedia, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(make_admin())
        self.client.raise_request_exception = False
        Location.objects.create(name='Top', site='S')

    def test_3d_coordinates_upload_500(self):
        coords = [p + [12.5] for p in SQ]
        f = SimpleUploadedFile('a.geojson', fc([feat('A', coords)]), content_type='application/geo+json')
        r = self.client.post(reverse('boundary_import_upload'), {'site': 'S', 'file': f})
        self.assertNotEqual(r.status_code, 500)

    def test_deep_nesting_upload_500(self):
        deep = '[' * 50000 + ']' * 50000
        raw = ('{"type":"Feature","geometry":{"type":"Polygon","coordinates":' + deep + '}}').encode()
        f = SimpleUploadedFile('a.geojson', raw, content_type='application/geo+json')
        r = self.client.post(reverse('boundary_import_upload'), {'site': 'S', 'file': f})
        self.assertNotEqual(r.status_code, 500)

    def test_create_new_duplicates(self):
        sq2 = [[x + 0.01, y] for x, y in SQ]
        f = SimpleUploadedFile('a.geojson', fc([feat('Top', SQ), feat('Top', sq2)]), content_type='application/geo+json')
        self.client.post(reverse('boundary_import_upload'), {'site': 'S', 'file': f})
        r = self.client.post(reverse('boundary_import_match'), {
            'shape_1': 'new', 'new_name_1': 'Top', 'shape_2': 'new', 'new_name_2': 'Top'})
        print('dup status', r.status_code, list(Location.objects.filter(site='S').values_list('name', flat=True)))
        self.assertEqual(Location.objects.filter(site='S', name='Top').count(), 1)



class Base(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name='O')
        self.rate = RateType.objects.create(name='R', daily_rate=10)
        self.a = Location.objects.create(name='A', site='S')
        self.b = Location.objects.create(name='B', site='S', usage=Location.Usage.RESTED)
        LocationUsagePeriod.objects.create(location=self.b, usage='rested', start_date=date(2025, 1, 1), source='auto')
        self.h = Horse.objects.create(name='H')
        self.today = timezone.localdate()


class PlacementEditProbes(Base):
    def test_edit_open_placement_location_leaves_usage_stale(self):
        p = PlacementService.arrive_horse(self.h, owner=self.owner, location=self.a, rate_type=self.rate,
                                          arrival_date=self.today - timedelta(days=30))
        self.a.refresh_from_db()
        self.assertEqual(self.a.usage, 'horses')
        self.client.force_login(make_admin())
        self.client.post(reverse('placement_update', args=[p.pk]), {
            'horse': self.h.pk, 'location': self.b.pk, 'rate_type': self.rate.pk,
            'start_date': p.start_date.isoformat(), 'end_date': '', 'expected_departure': '', 'notes': ''})
        p.refresh_from_db(); self.a.refresh_from_db(); self.b.refresh_from_db()
        self.assertEqual(p.location_id, self.b.pk)
        self.assertEqual(self.b.usage, 'horses')  # horse now on B
        self.assertEqual(self.a.usage, 'rested')  # A emptied


class PreviewPermProbe(Base):
    @override_settings(LOCATION_MAPS_ENABLED=True)
    def test_view_only_preview(self):
        u = make_user_with_access('v', locations='view', horses='view', dashboard='view')
        self.client.force_login(u)
        self.client.raise_request_exception = False
        self.client.get(reverse('location_list'))
        r = self.client.get(reverse('location_preview', args=[self.a.pk]),
                            HTTP_HX_REQUEST='true', HTTP_HX_TARGET='loc-rail-detail')
        self.assertEqual(r.status_code, 200)


class PhotoProbes(TempMedia, Base):
    def _img(self, name='p.jpg', color='red'):
        from PIL import Image
        import io
        b = io.BytesIO(); Image.new('RGB', (50, 50), color).save(b, 'JPEG')
        return SimpleUploadedFile(name, b.getvalue(), content_type='image/jpeg')

    def _files(self):
        out = []
        for root, _, fs in os.walk(self._media):
            out += [os.path.relpath(os.path.join(root, f), self._media) for f in fs]
        return sorted(out)


