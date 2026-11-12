"""Barns and stables on the other screens (docs/BARNS_AND_STABLES_PLAN.md, phase 4).

The last places that had to learn about stables: the Yard board's tile,
the map's marker, the search, the export, the horse's timeline — and the
plan itself, which now marks the stable of a horse the dashboard is
asking about.
"""

import csv
import io
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib import admin
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.dashboard import board
from core.models import (
    Horse, Location, Owner, Placement, RateType, StableStay, Turnout,
)
from core.roles_testutils import make_admin, make_user_with_access
from core.stables import StableService, add_stables, barn_plan, mark_attention
from core.turnout import TurnoutService
from health.models import FarrierVisit


class OtherScreensTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_admin()
        cls.owner = Owner.objects.create(name='Jo Bloggs')
        cls.rate = RateType.objects.create(name='Box care', daily_rate=Decimal('20'))
        cls.today = timezone.localdate()
        cls.barn = Location.objects.create(
            name='Top Barn', site='Manor Farm', location_type='barn', layout='aisle',
            latitude='51.100000', longitude='-0.200000')
        add_stables(cls.barn, 10)
        cls.paddock = Location.objects.create(
            name='Back Paddock', site='Manor Farm', capacity=6,
            latitude='51.101000', longitude='-0.201000')

    def setUp(self):
        self.client.force_login(self.admin)

    def stable(self, label):
        return self.barn.stables.get(label=str(label))

    def horse_in(self, location, name='Marram', days_ago=30):
        horse = Horse.objects.create(name=name)
        Placement.objects.create(
            horse=horse, owner=self.owner, location=location, rate_type=self.rate,
            start_date=self.today - timedelta(days=days_ago),
        )
        return horse


class BoardTests(OtherScreensTestCase):
    def test_a_barn_tile_says_how_its_horses_are_housed(self):
        for name, label in (('A', 1), ('B', 2)):
            StableService.assign(self.horse_in(self.barn, name), self.stable(label))
        self.horse_in(self.barn, 'C')
        tile = next(
            t for t in board.sites_overview(site='Manor Farm')[0]['tiles'] if t['is_barn'])
        self.assertEqual((tile['in_stables'], tile['no_stable'], tile['count']), (2, 1, 3))
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, '2 in stables')
        self.assertContains(response, '1 no stable')

    def test_a_barn_with_nobody_in_a_stable_reads_as_before(self):
        self.horse_in(self.barn)
        response = self.client.get(reverse('dashboard'))
        self.assertNotContains(response, 'in stables')

    def test_a_field_tile_counts_its_turnout(self):
        TurnoutService.start(self.horse_in(self.barn), self.paddock)
        self.horse_in(self.paddock, 'Resident')
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, '1 out by day')

    def test_the_board_costs_the_same_however_many_barns(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def queries():
            with CaptureQueriesContext(connection) as captured:
                board.sites_overview()
            return len(captured)

        StableService.assign(self.horse_in(self.barn), self.stable(1))
        few = queries()
        for index in range(5):
            extra = Location.objects.create(
                name=f'Barn {index}', site='Manor Farm', location_type='barn', layout='row')
            add_stables(extra, 3)
            StableService.assign(self.horse_in(extra, f'H{index}'), extra.stables.first())
        self.assertEqual(queries(), few)


class MapTests(OtherScreensTestCase):
    def test_the_map_script_draws_a_barn_as_a_square(self):
        script = (Path(settings.BASE_DIR) / 'static' / 'js' / 'location_map.js').read_text()
        self.assertIn('loc.is_barn', script)
        self.assertIn('L.rectangle', script)
        # The rule it must not break: an outline first, a point second.
        self.assertLess(script.index('if (loc.boundary)'), script.index('loc.is_barn'))

    def test_the_payload_says_which_points_are_barns(self):
        payload = board.map_locations('Manor Farm')
        flags = {loc['name']: loc['is_barn'] for loc in payload['locations']}
        self.assertEqual(flags, {'Top Barn': True, 'Back Paddock': False})


class SearchTests(OtherScreensTestCase):
    def find(self, query, user=None):
        if user is not None:
            self.client.force_login(user)
        return self.client.get(reverse('quick_find'), {'q': query}, HTTP_HX_REQUEST='true')

    def setUp(self):
        super().setUp()
        self.marram = self.horse_in(self.barn, 'Marram')
        StableService.assign(self.marram, self.stable(7))

    def test_barn_and_stable_finds_the_horse_in_it(self):
        response = self.find('top barn 7')
        self.assertEqual([h['name'] for h in response.context['horses']], ['Marram'])
        self.assertContains(response, 'Top Barn · 7')

    def test_part_of_the_barn_name_is_enough_and_the_word_stable_is_ignored(self):
        self.assertEqual([h['name'] for h in self.find('top 7').context['horses']], ['Marram'])
        self.assertEqual(
            [h['name'] for h in self.find('top barn stable 7').context['horses']], ['Marram'])

    def test_the_wrong_stable_or_barn_finds_nothing(self):
        self.assertEqual(self.find('top barn 8').context['horses'], [])
        self.assertEqual(self.find('lower barn 7').context['horses'], [])

    def test_a_horse_found_by_name_is_not_listed_twice(self):
        Horse.objects.filter(pk=self.marram.pk).update(name='Top 7')
        self.assertEqual(len(self.find('top 7').context['horses']), 1)

    def test_not_for_someone_who_cannot_see_locations(self):
        hand = make_user_with_access('hand', dashboard='full', horses='view')
        self.assertEqual(self.find('top barn 7', user=hand).context['horses'], [])

    @override_settings(BARNS_ENABLED=False)
    def test_switched_off(self):
        self.assertEqual(self.find('top barn 7').context['horses'], [])


class ExportTests(OtherScreensTestCase):
    def rows(self):
        response = self.client.get(reverse('horses_csv'))
        return list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))

    def test_stable_and_turnout_are_the_last_two_columns(self):
        horse = self.horse_in(self.barn)
        StableService.assign(horse, self.stable(7))
        TurnoutService.start(horse, self.paddock)
        self.horse_in(self.paddock, 'Resident')
        header, *rows = self.rows()
        self.assertEqual(header[:6], ['Name', 'Active', 'Date of birth', 'Sex', 'Colour', 'Owners'])
        self.assertEqual(header[6:], ['Stable', 'Turnout location'])
        by_name = {row[0]: row for row in rows}
        self.assertEqual(by_name['Marram'][6:], ['7', 'Back Paddock'])
        self.assertEqual(by_name['Resident'][6:], ['', ''])

    @override_settings(BARNS_ENABLED=False)
    def test_switched_off_the_file_is_as_it_was(self):
        self.horse_in(self.barn)
        self.assertEqual(
            self.rows()[0], ['Name', 'Active', 'Date of birth', 'Sex', 'Colour', 'Owners'])


class TimelineTests(OtherScreensTestCase):
    def setUp(self):
        super().setUp()
        self.horse = self.horse_in(self.barn)
        StableService.assign(self.horse, self.stable(7), self.today - timedelta(days=9))
        StableService.assign(self.horse, self.stable(9), self.today - timedelta(days=2))

    def test_each_stable_stay_is_its_own_row_on_the_horse_page(self):
        response = self.client.get(reverse('horse_detail', args=[self.horse.pk]))
        events = [e for e in response.context['timeline_events'] if e['type'] == 'stable']
        self.assertEqual([e['obj'].stable.label for e in events], ['9', '7'])
        self.assertContains(response, 'Stable 9 &middot; Top Barn')
        self.assertContains(response, 'Stable 7 &middot; Top Barn')
        # And they show under the Moves filter, with the placements.
        self.assertContains(response, "activeFilter === 'stable' || activeFilter === 'placement'")

    def test_and_in_the_preview_pane(self):
        response = self.client.get(
            reverse('horse_preview', args=[self.horse.pk]),
            HTTP_HX_REQUEST='true', HTTP_HX_TARGET='horse-preview-body')
        self.assertContains(response, 'Into stable 9')

    def test_a_rate_change_adds_no_stable_row(self):
        other = RateType.objects.create(name='Box rest', daily_rate=Decimal('30'))
        from core.services import PlacementService
        PlacementService.move_horse(
            self.horse, new_location=self.barn, move_date=self.today - timedelta(days=1),
            new_rate_type=other)
        response = self.client.get(reverse('horse_detail', args=[self.horse.pk]))
        events = [e for e in response.context['timeline_events'] if e['type'] == 'stable']
        self.assertEqual(len(events), 2)

    @override_settings(BARNS_ENABLED=False)
    def test_switched_off(self):
        response = self.client.get(reverse('horse_detail', args=[self.horse.pk]))
        self.assertFalse([e for e in response.context['timeline_events'] if e['type'] == 'stable'])


class AttentionDotTests(OtherScreensTestCase):
    def setUp(self):
        super().setUp()
        self.due = self.horse_in(self.barn, 'Overdue')
        self.fine = self.horse_in(self.barn, 'Fine')
        StableService.assign(self.due, self.stable(3))
        StableService.assign(self.fine, self.stable(4))
        FarrierVisit.objects.create(
            horse=self.due, date=self.today - timedelta(days=60), work_done='trim',
            next_due_date=self.today - timedelta(days=4))

    def test_a_reminder_the_yard_switched_off_marks_nothing(self):
        # The farrier reminder is off until a yard asks for it
        # (core/reminders.py). The plan must be as quiet as the dashboard.
        plan = mark_attention(barn_plan(self.barn), self.admin, site='Manor Farm')
        self.assertTrue(all(cell['attention'] == [] for cell in plan['cells']))

    def test_the_stable_of_a_horse_with_something_overdue_is_marked(self):
        from core.reminders import set_reminder
        set_reminder('farrier', True)
        plan = mark_attention(barn_plan(self.barn), self.admin, site='Manor Farm')
        marks = {cell['stable'].label: cell['attention'] for cell in plan['cells']}
        self.assertEqual(marks['3'], ['Farrier'])
        self.assertEqual(marks['4'], [])
        response = self.client.get(reverse('location_detail', args=[self.barn.pk]))
        self.assertContains(response, 'barn-cell-flag', count=1)
        self.assertContains(response, 'Needs attention: Farrier')

    def test_only_for_what_the_reader_may_see(self):
        from core.reminders import set_reminder
        set_reminder('farrier', True)
        looker = make_user_with_access('looker', locations='view', horses='view')
        plan = mark_attention(barn_plan(self.barn), looker, site='Manor Farm')
        self.assertTrue(all(cell['attention'] == [] for cell in plan['cells']))


class AdminTests(OtherScreensTestCase):
    def test_the_new_records_are_in_the_admin(self):
        self.assertIn(StableStay, admin.site._registry)
        self.assertIn(Turnout, admin.site._registry)
