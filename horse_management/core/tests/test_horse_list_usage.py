"""Land use on the horse list, and the batched query behind it.

The usage strip is the reason the entity spine was worth building: a
rested field has no horses, so a horse-built list could never say how
long it had been resting. Getting it costs one query for the whole page,
not one per field — these tests hold that line.
"""

from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Horse, Location, LocationUsagePeriod, Owner, Placement, RateType,
)
from core.roles_testutils import make_admin, make_user_with_access
from core.views.locations import usage_days_for_locations


class UsageStripTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_admin('usage')
        cls.rate = RateType.objects.create(name='Full', daily_rate=10)
        cls.owner = Owner.objects.create(name='Alice Appleby')

        cls.barn = Location.objects.create(
            name='Big Barn', site='Colgate', capacity=4,
        )
        cls.resting = Location.objects.create(
            name='Bottom Field', site='Colgate', capacity=6,
            usage=Location.Usage.RESTED,
        )
        cls.untracked = Location.objects.create(
            name='New Field', site='Colgate',
        )

        today = timezone.localdate()
        horse = Horse.objects.create(name='Zara')
        Placement.objects.create(
            horse=horse, owner=cls.owner, location=cls.barn,
            rate_type=cls.rate, start_date=today - timedelta(days=200),
        )
        # 40 days rested, then hay to date — inside a 3-month window.
        LocationUsagePeriod.objects.create(
            location=cls.resting, usage=Location.Usage.RESTED,
            start_date=today - timedelta(days=60),
            end_date=today - timedelta(days=21),
        )
        LocationUsagePeriod.objects.create(
            location=cls.resting, usage=Location.Usage.HAY,
            start_date=today - timedelta(days=20),
        )
        # The barn needs no explicit period: the placement above opens a
        # HORSES period automatically (LocationUsageService).

    def setUp(self):
        self.client.force_login(self.user)

    def _groups(self, **params):
        response = self.client.get(reverse('horse_list'), params)
        self.assertEqual(response.status_code, 200)
        return {g['name']: g for g in response.context['grouped_horses']}

    # ── The window ───────────────────────────────────────────────────

    def test_window_defaults_to_the_last_three_months(self):
        """Not the calendar year the analytics tab opens on."""
        response = self.client.get(
            reverse('horse_list'), {'group_by': 'location'},
        )
        self.assertEqual(response.context['usage_range'], '3mo')
        self.assertEqual(
            response.context['usage_period_label'], 'last 3 months',
        )

    def test_window_can_be_widened(self):
        response = self.client.get(
            reverse('horse_list'), {'group_by': 'location', 'range': '6mo'},
        )
        self.assertEqual(response.context['usage_range'], '6mo')

    def test_unknown_window_falls_back_to_the_default(self):
        response = self.client.get(
            reverse('horse_list'), {'group_by': 'location', 'range': 'decade'},
        )
        self.assertEqual(response.context['usage_range'], '3mo')

    # ── Which axes carry it ──────────────────────────────────────────

    def test_land_axes_carry_the_strip(self):
        for layout in ('location', 'site'):
            with self.subTest(layout=layout):
                response = self.client.get(
                    reverse('horse_list'),
                    {'group_by': 'location', 'layout': layout},
                )
                self.assertTrue(response.context['shows_usage'])

    def test_owner_and_all_do_not(self):
        """Land use says nothing about an owner."""
        for axis in ('all', 'owner'):
            with self.subTest(axis=axis):
                response = self.client.get(
                    reverse('horse_list'), {'group_by': axis},
                )
                self.assertFalse(response.context['shows_usage'])
                for group in response.context['grouped_horses']:
                    self.assertNotIn('usage_strip', group)

    # ── The numbers ──────────────────────────────────────────────────

    def test_rested_field_reports_its_rest_and_hay_days(self):
        strip = self._groups(group_by='location')['Bottom Field']['usage_strip']
        days = {s['value']: s['days'] for s in strip}
        self.assertEqual(days[Location.Usage.RESTED], 40)
        self.assertEqual(days[Location.Usage.HAY], 21)

    def test_strip_is_ordered_widest_share_first(self):
        strip = self._groups(group_by='location')['Bottom Field']['usage_strip']
        self.assertEqual([s['days'] for s in strip], [40, 21])
        self.assertAlmostEqual(sum(s['pct'] for s in strip), 100, places=0)

    def test_a_field_with_no_recorded_use_gets_an_empty_strip(self):
        """No bar rather than an empty one: untracked is not idle."""
        self.assertEqual(
            self._groups(group_by='location')['New Field']['usage_strip'], [],
        )

    def test_site_strip_sums_its_locations(self):
        colgate = self._groups(group_by='location', layout='site')['Colgate']
        days = {s['value']: s['days'] for s in colgate['usage_strip']}
        self.assertEqual(days[Location.Usage.RESTED], 40)
        self.assertEqual(days[Location.Usage.HAY], 21)
        self.assertIn(Location.Usage.HORSES, days)

    def test_strip_renders_with_its_day_counts(self):
        response = self.client.get(
            reverse('horse_list'), {'group_by': 'location'},
        )
        self.assertContains(response, '40d')
        self.assertContains(response, 'No land use recorded')

    def test_no_raw_template_comment_leaks_into_the_page(self):
        """A {# #} comment only works on one line; a wrapped one renders
        as body text. It did, on the mixed-use card."""
        for layout in ('location', 'site'):
            with self.subTest(layout=layout):
                response = self.client.get(
                    reverse('horse_list'),
                    {'group_by': 'location', 'layout': layout},
                )
                self.assertNotContains(response, '{#')

    # ── Change-use trigger ───────────────────────────────────────────

    def test_change_use_trigger_renders_for_a_full_access_role(self):
        response = self.client.get(
            reverse('horse_list'), {'group_by': 'location'},
        )
        self.assertContains(response, 'Change use')
        self.assertContains(
            response, reverse('location_set_usage', args=[self.barn.pk]),
        )

    def test_change_use_trigger_hidden_for_a_view_only_role(self):
        viewer = make_user_with_access(
            'looker', horses='view', locations='view',
        )
        self.client.force_login(viewer)
        response = self.client.get(
            reverse('horse_list'), {'group_by': 'location'},
        )
        self.assertNotContains(response, 'Change use')


class BatchedUsageQueryTests(TestCase):
    """One query for every location on the page, not one each."""

    @classmethod
    def setUpTestData(cls):
        cls.user = make_admin('batcher')
        today = timezone.localdate()
        cls.locations = []
        for i in range(20):
            location = Location.objects.create(
                name=f'Field {i:02d}', site=f'Site {i % 3}',
            )
            LocationUsagePeriod.objects.create(
                location=location, usage=Location.Usage.RESTED,
                start_date=today - timedelta(days=90),
            )
            cls.locations.append(location)

    def setUp(self):
        self.client.force_login(self.user)

    def test_helper_reads_every_location_in_one_query(self):
        today = timezone.localdate()
        ids = [loc.pk for loc in self.locations]
        with CaptureQueriesContext(connection) as ctx:
            result = usage_days_for_locations(
                ids, today - timedelta(days=30), today,
            )
        self.assertEqual(len(ctx.captured_queries), 1)
        self.assertEqual(set(result), set(ids))

    def test_helper_answers_for_locations_with_no_periods(self):
        """Every id asked for comes back, recorded or not."""
        today = timezone.localdate()
        spare = Location.objects.create(name='Spare', site='Site 0')
        result = usage_days_for_locations(
            [spare.pk], today - timedelta(days=30), today,
        )
        totals, segments = result[spare.pk]
        self.assertEqual(sum(totals.values()), 0)
        self.assertEqual(segments, [])

    def test_helper_makes_no_query_for_an_empty_id_list(self):
        today = timezone.localdate()
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(usage_days_for_locations([], today, today), {})
        self.assertEqual(len(ctx.captured_queries), 0)

    def test_the_strip_costs_one_query_for_twenty_fields(self):
        def count(**params):
            with CaptureQueriesContext(connection) as ctx:
                response = self.client.get(reverse('horse_list'), params)
            self.assertEqual(response.status_code, 200)
            return len(ctx.captured_queries)

        without = count(group_by='owner')
        with_strip = count(group_by='location')
        self.assertLessEqual(with_strip, without + 2)

    def test_locations_usage_tab_is_batched_too(self):
        """The tab this helper was extracted from must benefit as well."""
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(
                reverse('location_list'), {'tab': 'usage'},
            )
        self.assertEqual(response.status_code, 200)
        usage_queries = [
            q for q in ctx.captured_queries
            if 'usageperiod' in q['sql'].lower()
        ]
        self.assertLessEqual(len(usage_queries), 2)
