"""Tests for field (Location) usage history tracking and analytics.

Covers the LocationUsageService writer, the automatic transitions driven by
horse arrivals/departures, and the usage_days_for_year analytics helper.
"""

from datetime import date, datetime, timezone as dt_timezone
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from core.models import (
    Horse, Location, LocationUsagePeriod, Owner, Placement, RateType,
)
from core.services import LocationUsageService, PlacementService
from core.views.locations import usage_days_for_year


class SetUsageTests(TestCase):
    def setUp(self):
        self.loc = Location.objects.create(name='Top Field', site='Main')
        # Seed an open period as the migration backfill would.
        LocationUsagePeriod.objects.create(
            location=self.loc, usage=Location.Usage.RESTED,
            start_date=date(2026, 1, 1), source='auto',
        )

    def test_set_usage_closes_prior_and_opens_new(self):
        period = LocationUsageService.set_usage(
            self.loc, usage=Location.Usage.HAY, change_date=date(2026, 4, 1),
        )
        self.assertEqual(period.usage, Location.Usage.HAY)
        self.assertEqual(period.start_date, date(2026, 4, 1))
        self.assertIsNone(period.end_date)

        prior = self.loc.usage_periods.get(usage=Location.Usage.RESTED)
        self.assertEqual(prior.end_date, date(2026, 3, 31))

        self.loc.refresh_from_db()
        self.assertEqual(self.loc.usage, Location.Usage.HAY)

    def test_no_op_when_usage_unchanged(self):
        result = LocationUsageService.set_usage(
            self.loc, usage=Location.Usage.RESTED, change_date=date(2026, 6, 1),
        )
        self.assertIsNone(result)
        self.assertEqual(self.loc.usage_periods.count(), 1)

    def test_change_date_not_after_current_start_raises(self):
        with self.assertRaises(ValidationError):
            LocationUsageService.set_usage(
                self.loc, usage=Location.Usage.HAY, change_date=date(2026, 1, 1),
            )

    def test_only_one_open_period_allowed(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LocationUsagePeriod.objects.create(
                    location=self.loc, usage=Location.Usage.HAY,
                    start_date=date(2026, 5, 1),
                )


class UsageDaysForYearTests(TestCase):
    def setUp(self):
        self.loc = Location.objects.create(name='Spanning', site='Main')

    def test_clips_across_year_boundaries_and_sums_to_year(self):
        # Rested all of 2025 into mid-2026, then horses for the rest of 2026.
        LocationUsagePeriod.objects.create(
            location=self.loc, usage=Location.Usage.RESTED,
            start_date=date(2025, 6, 1), end_date=date(2026, 6, 30),
        )
        LocationUsagePeriod.objects.create(
            location=self.loc, usage=Location.Usage.HORSES,
            start_date=date(2026, 7, 1),  # open
        )
        totals, segments = usage_days_for_year(self.loc, 2026)
        # Jan 1–Jun 30 = 181 days rested; Jul 1–Dec 31 = 184 days horses.
        self.assertEqual(totals[Location.Usage.RESTED], 181)
        self.assertEqual(totals[Location.Usage.HORSES], 184)
        self.assertEqual(sum(totals.values()), 365)
        self.assertEqual(len(segments), 2)

    def test_empty_year_returns_zeroes(self):
        totals, segments = usage_days_for_year(self.loc, 2020)
        self.assertEqual(sum(totals.values()), 0)
        self.assertEqual(segments, [])


class AutoTransitionTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name='Owner')
        self.rate = RateType.objects.create(name='Grass', daily_rate=10)
        self.loc = Location.objects.create(
            name='Paddock', site='Main', usage=Location.Usage.RESTED,
        )
        LocationUsagePeriod.objects.create(
            location=self.loc, usage=Location.Usage.RESTED,
            start_date=date(2026, 1, 1), source='auto',
        )
        self.horse = Horse.objects.create(name='Dobbin')

    def test_arrival_onto_empty_field_marks_horses(self):
        PlacementService.arrive_horse(
            self.horse, owner=self.owner, location=self.loc,
            rate_type=self.rate, arrival_date=date(2026, 3, 1),
        )
        self.loc.refresh_from_db()
        self.assertEqual(self.loc.usage, Location.Usage.HORSES)
        open_period = self.loc.usage_periods.get(end_date__isnull=True)
        self.assertEqual(open_period.usage, Location.Usage.HORSES)
        self.assertEqual(open_period.source, 'auto')

    def test_last_departure_rests_field(self):
        PlacementService.arrive_horse(
            self.horse, owner=self.owner, location=self.loc,
            rate_type=self.rate, arrival_date=date(2026, 3, 1),
        )
        PlacementService.depart_horse(self.horse, date(2026, 5, 31))
        self.loc.refresh_from_db()
        self.assertEqual(self.loc.usage, Location.Usage.RESTED)
        open_period = self.loc.usage_periods.get(end_date__isnull=True)
        # Field empty from the day after the final occupied day.
        self.assertEqual(open_period.start_date, date(2026, 6, 1))

    def test_manual_state_preserved_while_occupied(self):
        # Two horses arrive; field becomes horses.
        h2 = Horse.objects.create(name='Trigger')
        PlacementService.arrive_horse(
            self.horse, owner=self.owner, location=self.loc,
            rate_type=self.rate, arrival_date=date(2026, 3, 1),
        )
        # Manually flag the occupied field as mixed grazing.
        LocationUsageService.set_usage(
            self.loc, usage=Location.Usage.MIXED, change_date=date(2026, 3, 15),
        )
        # A second horse arriving must NOT clobber the manual 'mixed' state.
        PlacementService.arrive_horse(
            h2, owner=self.owner, location=self.loc,
            rate_type=self.rate, arrival_date=date(2026, 3, 20),
        )
        self.loc.refresh_from_db()
        self.assertEqual(self.loc.usage, Location.Usage.MIXED)


class BackfillCommandTests(TestCase):
    """Reconstructing usage history from placement records."""

    def setUp(self):
        self.owner = Owner.objects.create(name='Owner')
        self.rate = RateType.objects.create(name='Grass', daily_rate=10)
        self.today = timezone.now().date()

    def _make_location(self, name, created):
        loc = Location.objects.create(name=name, site='Main')
        # created_at is auto_now_add; override via queryset update.
        Location.objects.filter(pk=loc.pk).update(
            created_at=datetime(created.year, created.month, created.day, tzinfo=dt_timezone.utc)
        )
        # Seed the open period the migration would have created.
        LocationUsagePeriod.objects.create(
            location=loc, usage=loc.usage, start_date=created, source='auto',
        )
        return Location.objects.get(pk=loc.pk)

    def _place(self, loc, name, start, end):
        horse = Horse.objects.create(name=name)
        Placement.objects.create(
            horse=horse, owner=self.owner, location=loc,
            rate_type=self.rate, start_date=start, end_date=end,
        )

    def _run(self, *args):
        call_command('backfill_location_usage', *args, stdout=StringIO())

    def test_rebuilds_horses_and_rest_spans(self):
        loc = self._make_location('Top', date(2024, 1, 1))
        # Horses Mar–May 2024 (ended), then empty, then horses ongoing.
        self._place(loc, 'A', date(2024, 3, 1), date(2024, 5, 31))
        self._place(loc, 'B', date(2024, 9, 1), None)

        self._run('--apply')
        periods = list(loc.usage_periods.order_by('start_date'))
        # rested(Jan1–Feb29) horses(Mar1–May31) rested(Jun1–Aug31) horses(Sep1–open)
        self.assertEqual(len(periods), 4)
        self.assertEqual(
            [p.usage for p in periods],
            ['rested', 'horses', 'rested', 'horses'],
        )
        self.assertEqual(periods[0].start_date, date(2024, 1, 1))
        self.assertEqual(periods[1].start_date, date(2024, 3, 1))
        self.assertEqual(periods[1].end_date, date(2024, 5, 31))
        self.assertEqual(periods[2].start_date, date(2024, 6, 1))
        self.assertIsNone(periods[-1].end_date)  # ongoing → open horses
        loc.refresh_from_db()
        self.assertEqual(loc.usage, Location.Usage.HORSES)

    def test_one_day_gap_is_a_rest_day(self):
        loc = self._make_location('Gap', date(2024, 1, 1))
        self._place(loc, 'A', date(2024, 3, 1), date(2024, 3, 10))
        # 2-day gap (11th, 12th empty), then horses again.
        self._place(loc, 'B', date(2024, 3, 13), date(2024, 3, 20))
        self._run('--apply')
        rest = loc.usage_periods.filter(
            usage='rested', start_date=date(2024, 3, 11)
        ).first()
        self.assertIsNotNone(rest)
        self.assertEqual(rest.end_date, date(2024, 3, 12))

    def test_contiguous_placements_merge(self):
        loc = self._make_location('Merge', date(2024, 1, 1))
        # End 10th, next starts 11th — no empty day, single horses span.
        self._place(loc, 'A', date(2024, 3, 1), date(2024, 3, 10))
        self._place(loc, 'B', date(2024, 3, 11), date(2024, 3, 20))
        self._run('--apply')
        horses = loc.usage_periods.filter(usage='horses')
        self.assertEqual(horses.count(), 1)
        self.assertEqual(horses.first().start_date, date(2024, 3, 1))
        self.assertEqual(horses.first().end_date, date(2024, 3, 20))

    def test_no_placements_left_untouched(self):
        loc = self._make_location('Empty', date(2024, 1, 1))
        loc.usage = Location.Usage.HAY
        loc.save()
        LocationUsagePeriod.objects.filter(location=loc).update(usage='hay')
        self._run('--apply')
        periods = list(loc.usage_periods.all())
        self.assertEqual(len(periods), 1)
        self.assertEqual(periods[0].usage, 'hay')

    def test_manual_history_skipped_without_force(self):
        loc = self._make_location('Manual', date(2024, 1, 1))
        self._place(loc, 'A', date(2024, 3, 1), None)
        LocationUsageService.set_usage(
            loc, usage=Location.Usage.HAY, change_date=date(2024, 6, 1),
        )
        before = loc.usage_periods.count()
        self._run('--apply')
        self.assertEqual(loc.usage_periods.count(), before)
        # With --force it rebuilds, dropping the manual period.
        self._run('--apply', '--force')
        self.assertFalse(loc.usage_periods.filter(source='manual').exists())

    def test_dry_run_writes_nothing(self):
        loc = self._make_location('Dry', date(2024, 1, 1))
        self._place(loc, 'A', date(2024, 3, 1), date(2024, 5, 31))
        before = loc.usage_periods.count()
        self._run()  # no --apply
        self.assertEqual(loc.usage_periods.count(), before)
