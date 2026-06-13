"""Tests for field (Location) usage history tracking and analytics.

Covers the LocationUsageService writer, the automatic transitions driven by
horse arrivals/departures, and the usage_days_for_year analytics helper.
"""

from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

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
