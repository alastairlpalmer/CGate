"""Locations, imports and horse photos: third bug hunt (each test failed before the fix)."""
from django.test import TestCase, override_settings
from django.urls import reverse
from core.models import Horse, Location
from core.roles_testutils import make_viewer


@override_settings(LOCATION_MAPS_ENABLED=True)
class ViewerPreviews(TestCase):
    def test_viewer(self):
        h = Horse.objects.create(name='H'); loc = Location.objects.create(name='A', site='S', latitude=51, longitude=-1)
        self.client.force_login(make_viewer())
        self.client.raise_request_exception = False
        self.client.get(reverse('horse_list'))
        hp = self.client.get(reverse('horse_preview', args=[h.pk]), HTTP_HX_REQUEST='true', HTTP_HX_TARGET='horse-preview-body')
        self.client.get(reverse('location_list'))
        lp = self.client.get(reverse('location_preview', args=[loc.pk]), HTTP_HX_REQUEST='true', HTTP_HX_TARGET='loc-rail-detail')
        self.assertEqual(hp.status_code, 200)
        self.assertEqual(lp.status_code, 200)
