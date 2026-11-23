"""Locations with no map boundary: the sort reaches them.

The Locations page lists the locations that have no boundary in a block
of their own, "Not on the map", after the mapped ones. Until November
2026 the two sort buttons ("Most horses", "Longest rested") moved only
the mapped rows. On a site with no map the block is the whole list, so
the buttons did nothing there.
"""

import re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, LocationUsagePeriod, Owner, Placement, RateType
from core.roles_testutils import make_admin
from core.services import LocationUsageService


@override_settings(LOCATION_MAPS_ENABLED=True)
class UnmappedOrderTests(TestCase):
    def setUp(self):
        self.client.force_login(make_admin('boss'))
        self.today = timezone.localdate()
        owner = Owner.objects.create(name='Helen Marsh')
        rate = RateType.objects.create(name='Grass', daily_rate=Decimal('6.00'))
        # No location of this site has a boundary. Names in A to Z order
        # are not the order of the horses.
        self.alder = Location.objects.create(name='Alder', site='Mill Lane')
        self.birch = Location.objects.create(name='Birch', site='Mill Lane')
        self.cedar = Location.objects.create(name='Cedar', site='Mill Lane')
        for location, horses in ((self.birch, 1), (self.cedar, 3)):
            for number in range(horses):
                Placement.objects.create(
                    horse=Horse.objects.create(name=f'{location.name} {number}'),
                    owner=owner, location=location, rate_type=rate,
                    start_date=self.today - timedelta(days=40),
                )
        LocationUsageService.set_usage(
            self.alder, usage=Location.Usage.RESTED,
            change_date=self.today - timedelta(days=21),
            source=LocationUsagePeriod.Source.MANUAL,
        )

    def names(self, body, block_start):
        block = body.split(block_start, 1)[1].split('</div>\n', 1)[0]
        return re.findall(r'data-name="([^"]+)" data-unmapped', block)

    def body(self):
        return self.client.get(reverse('location_list')).content.decode()

    def test_the_block_opens_with_most_horses_first(self):
        # "Most horses" is the sort that is on when the page opens.
        body = self.body()
        self.assertIn('class="loc-sort is-active" data-loc-sort="horses"', body)
        phone = body.split('data-loc-sheet', 1)[1]
        self.assertEqual(self.names(phone, '<div data-loc-unmapped>'), ['Cedar', 'Birch', 'Alder'])

    def test_a_row_says_for_how_long_it_is_rested_as_a_mapped_row_does(self):
        body = self.body()
        row = body.split(f'data-pk="{self.alder.pk}"', 1)[1].split('</button>', 1)[0]
        self.assertIn('data-rest="21"', row)
        self.assertRegex(row, r'Rested\s+21 days')

    def test_both_scripts_sort_the_block(self):
        # The block keeps its place at the end, and its rows are put in
        # the order of the sort that is on.
        for name, rows in (('locations_mobile.js', 'rows'), ('locations_board.js', 'looseRows')):
            script = (Path(settings.BASE_DIR) / 'static' / 'js' / name).read_text()
            block = script.split("querySelector('[data-loc-unmapped]')", 1)[1].split('unmapped-count', 1)[0]
            self.assertIn(f'{rows}.sort(function', block, name)
            self.assertIn('compareRows(state.sort', block, name)
            self.assertIn('appendChild(r.el)', block, name)
