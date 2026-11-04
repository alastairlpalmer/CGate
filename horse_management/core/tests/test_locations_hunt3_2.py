"""Locations, imports and horse photos: third bug hunt (each test failed before the fix)."""
from datetime import date, timedelta
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from core.models import Horse, Location, LocationUsagePeriod, Owner, Placement, RateType
from core.roles_testutils import make_admin
from core.services import PlacementService


class MisclickDelete(TestCase):
    def test_delete_misclick_resets_rest(self):
        owner = Owner.objects.create(name='O'); rate = RateType.objects.create(name='R', daily_rate=10)
        b = Location.objects.create(name='B', site='S', usage='rested')
        today = timezone.localdate()
        LocationUsagePeriod.objects.create(location=b, usage='rested', start_date=today - timedelta(days=60), source='auto')
        h = Horse.objects.create(name='H')
        p = PlacementService.arrive_horse(h, owner=owner, location=b, rate_type=rate, arrival_date=today)
        self.client.force_login(make_admin())
        self.client.post(reverse('placement_delete', args=[p.pk]))
        b.refresh_from_db()
        list(b.usage_periods.order_by('start_date').values_list('usage', 'start_date', 'end_date', 'source'))
        self.client.get(reverse('location_list'))
        from core.dashboard import board
        cur = b.usage_periods.get(end_date__isnull=True)
        self.assertEqual(cur.start_date, today - timedelta(days=60))

