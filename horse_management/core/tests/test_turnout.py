"""Day turnout (docs/BARNS_AND_STABLES_PLAN.md, phase 3).

A stabled horse can have one standing turnout location. These are the
rules that matter: it follows the horse while the horse lives in *a*
barn; and the field it is on knows it is in use — the rows of the table
in section 8.3 of the plan.
"""

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from core.models import (
    Horse, Location, LocationUsagePeriod, Owner, Placement, RateType, Turnout,
)
from core.services import LocationUsageService, PlacementService
from core.stables import add_stables
from core.turnout import (
    TurnoutService, annotate_turnout, open_turnout, stray_turnouts,
    turnout_choices, turnout_counts, turnout_horses,
)

USAGE = Location.Usage
AUTO = LocationUsagePeriod.Source.AUTO


class TurnoutTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = Owner.objects.create(name='Jo Bloggs')
        cls.rate = RateType.objects.create(name='Box care', daily_rate=Decimal('20'))
        cls.today = timezone.localdate()
        cls.barn = Location.objects.create(
            name='Top Barn', site='Manor Farm', location_type='barn', layout='aisle')
        add_stables(cls.barn, 6)
        cls.other_barn = Location.objects.create(
            name='Lower Barn', site='Manor Farm', location_type='barn', layout='row')
        cls.paddock = Location.objects.create(
            name='Back Paddock', site='Manor Farm', capacity=6, usage=USAGE.RESTED)
        cls.hill = Location.objects.create(
            name='Hill Field', site='Manor Farm', capacity=8, usage=USAGE.RESTED)

    def day(self, days_ago):
        return self.today - timedelta(days=days_ago)

    def horse_in(self, location, name='Marram', days_ago=30):
        horse = Horse.objects.create(name=name)
        Placement.objects.create(
            horse=horse, owner=self.owner, location=location, rate_type=self.rate,
            start_date=self.day(days_ago),
        )
        return horse

    def fresh(self, horse):
        return Horse.objects.get(pk=horse.pk)

    def usage(self, location):
        location.refresh_from_db()
        return location.usage


class StartStopTests(TurnoutTestCase):
    def test_turn_a_stabled_horse_out(self):
        horse = self.horse_in(self.barn)
        turnout = TurnoutService.start(horse, self.paddock, self.day(5))
        self.assertEqual((turnout.location, turnout.start_date, turnout.end_date),
                         (self.paddock, self.day(5), None))
        self.assertEqual(open_turnout(horse), turnout)
        # It still lives in the barn, on the same placement.
        self.assertEqual(horse.placements.get().location, self.barn)

    def test_only_a_horse_that_lives_in_a_barn(self):
        with self.assertRaisesMessage(ValidationError, 'does not live in a barn'):
            TurnoutService.start(self.horse_in(self.hill), self.paddock)
        with self.assertRaises(ValidationError):
            TurnoutService.start(Horse.objects.create(name='Nowhere'), self.paddock)

    def test_not_onto_a_barn_or_an_archived_location(self):
        horse = self.horse_in(self.barn)
        with self.assertRaisesMessage(ValidationError, 'is a barn'):
            TurnoutService.start(horse, self.other_barn)
        self.hill.is_archived = True
        self.hill.save()
        with self.assertRaises(ValidationError):
            TurnoutService.start(horse, self.hill)

    def test_dates_that_cannot_be(self):
        horse = self.horse_in(self.barn, days_ago=10)
        with self.assertRaises(ValidationError):
            TurnoutService.start(horse, self.paddock, self.today + timedelta(days=1))
        with self.assertRaises(ValidationError):
            TurnoutService.start(horse, self.paddock, self.day(11))

    def test_changing_the_location_ends_the_old_turnout_the_day_before(self):
        horse = self.horse_in(self.barn)
        first = TurnoutService.start(horse, self.paddock, self.day(9))
        second = TurnoutService.change(horse, self.hill, self.day(2))
        first.refresh_from_db()
        self.assertEqual(first.end_date, self.day(3))
        self.assertEqual(open_turnout(horse), second)

    def test_the_same_location_again_changes_nothing(self):
        horse = self.horse_in(self.barn)
        first = TurnoutService.start(horse, self.paddock, self.day(9))
        self.assertEqual(TurnoutService.start(horse, self.paddock), first)
        self.assertEqual(Turnout.objects.count(), 1)

    def test_stop(self):
        horse = self.horse_in(self.barn)
        TurnoutService.start(horse, self.paddock, self.day(9))
        ended = TurnoutService.stop(horse, self.today)
        self.assertEqual(ended.end_date, self.day(1))
        self.assertIsNone(open_turnout(horse))
        self.assertIsNone(TurnoutService.stop(horse))

    def test_one_open_turnout_a_horse_is_a_database_rule(self):
        from django.db import IntegrityError, transaction
        horse = self.horse_in(self.barn)
        TurnoutService.start(horse, self.paddock)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Turnout.objects.create(horse=horse, location=self.hill, start_date=self.today)


class FieldUsageTests(TurnoutTestCase):
    """Section 8.3: the field must know it is in use."""

    def test_the_first_turnout_onto_empty_ground_makes_it_turnout(self):
        horse = self.horse_in(self.barn)
        TurnoutService.start(horse, self.paddock, self.day(5))
        self.assertEqual(self.usage(self.paddock), USAGE.TURNOUT)
        period = self.paddock.usage_periods.get(end_date__isnull=True)
        self.assertEqual((period.usage, period.start_date, period.source), (USAGE.TURNOUT, self.day(5), AUTO))
        self.assertFalse(LocationUsageService._is_empty(self.paddock))

    def test_the_last_turnout_ending_rests_it_from_the_next_day(self):
        horse = self.horse_in(self.barn)
        TurnoutService.start(horse, self.paddock, self.day(9))
        TurnoutService.stop(horse, self.day(2))
        self.assertEqual(self.usage(self.paddock), USAGE.RESTED)
        self.assertEqual(self.paddock.usage_periods.get(end_date__isnull=True).start_date, self.day(2))

    def test_one_of_two_ending_leaves_it_in_use(self):
        a, b = self.horse_in(self.barn, 'A'), self.horse_in(self.barn, 'B')
        TurnoutService.start(a, self.paddock, self.day(9))
        TurnoutService.start(b, self.paddock, self.day(9))
        TurnoutService.stop(a, self.day(2))
        self.assertEqual(self.usage(self.paddock), USAGE.TURNOUT)

    def test_a_resident_arriving_makes_it_horses(self):
        TurnoutService.start(self.horse_in(self.barn), self.paddock, self.day(9))
        self.horse_in(self.paddock, 'Resident', days_ago=4)
        self.assertEqual(self.usage(self.paddock), USAGE.HORSES)

    def test_the_last_resident_leaving_makes_it_turnout_again_not_rested(self):
        TurnoutService.start(self.horse_in(self.barn), self.paddock, self.day(9))
        resident = self.horse_in(self.paddock, 'Resident', days_ago=6)
        PlacementService.depart_horse(resident, self.day(2))
        self.assertEqual(self.usage(self.paddock), USAGE.TURNOUT)
        self.assertEqual(self.paddock.usage_periods.get(end_date__isnull=True).start_date, self.day(1))

    def test_turnout_onto_ground_with_residents_changes_nothing(self):
        self.horse_in(self.paddock, 'Resident', days_ago=20)
        self.assertEqual(self.usage(self.paddock), USAGE.HORSES)
        TurnoutService.start(self.horse_in(self.barn), self.paddock, self.day(5))
        self.assertEqual(self.usage(self.paddock), USAGE.HORSES)

    def test_a_usage_set_by_hand_is_left_alone(self):
        self.horse_in(self.paddock, 'Resident', days_ago=20)
        LocationUsageService.set_usage(self.paddock, usage=USAGE.MIXED, change_date=self.day(10))
        horse = self.horse_in(self.barn)
        TurnoutService.start(horse, self.paddock, self.day(5))
        self.assertEqual(self.usage(self.paddock), USAGE.MIXED)

    def test_turnout_can_be_chosen_by_hand(self):
        LocationUsageService.set_usage(self.hill, usage=USAGE.TURNOUT, change_date=self.day(3))
        self.assertEqual(self.usage(self.hill), USAGE.TURNOUT)

    def test_a_turnout_started_and_changed_the_same_day_leaves_no_false_history(self):
        horse = self.horse_in(self.barn)
        TurnoutService.start(horse, self.paddock, self.today)
        TurnoutService.change(horse, self.hill, self.today)
        # The paddock never had it: back to the rest it was in, no blip.
        self.assertEqual(self.usage(self.paddock), USAGE.RESTED)
        self.assertFalse(self.paddock.usage_periods.filter(usage=USAGE.TURNOUT).exists())
        self.assertEqual(self.usage(self.hill), USAGE.TURNOUT)
        self.assertEqual(Turnout.objects.count(), 1)

    def test_ground_with_turnout_cannot_be_archived(self):
        TurnoutService.start(self.horse_in(self.barn), self.paddock)
        self.assertTrue(any('out here by day' in reason or 'goes out' in reason
                            for reason in self.paddock.archive_blockers()))
        self.assertEqual(self.hill.archive_blockers(), [])

    def test_ground_with_turnout_history_cannot_be_deleted(self):
        horse = self.horse_in(self.barn)
        TurnoutService.start(horse, self.paddock, self.day(9))
        TurnoutService.stop(horse, self.day(2))
        self.assertTrue(any('turnout record' in reason for reason in self.paddock.delete_blockers()))


class FollowsThePlacementTests(TurnoutTestCase):
    def setUp(self):
        self.horse = self.horse_in(self.barn)
        self.turnout = TurnoutService.start(self.horse, self.paddock, self.day(20))

    def reload(self):
        self.turnout.refresh_from_db()
        return self.turnout

    def test_departure_ends_it_and_rests_the_field(self):
        PlacementService.depart_horse(self.horse, self.day(1))
        turnout = self.reload()
        self.assertEqual(turnout.end_date, self.day(1))
        self.assertTrue(turnout.closed_with_placement)
        self.assertEqual(self.usage(self.paddock), USAGE.RESTED)

    def test_undoing_a_departure_gives_it_back_and_un_rests_the_field(self):
        PlacementService.depart_horse(self.horse, self.day(1))
        PlacementService.cancel_departure(self.fresh(self.horse))
        self.assertIsNone(self.reload().end_date)
        self.assertEqual(self.usage(self.paddock), USAGE.TURNOUT)
        self.assertEqual(
            self.paddock.usage_periods.get(end_date__isnull=True).start_date, self.day(20))

    def test_a_rate_change_keeps_it(self):
        other = RateType.objects.create(name='Box rest', daily_rate=Decimal('30'))
        PlacementService.move_horse(
            self.horse, new_location=self.barn, move_date=self.day(3), new_rate_type=other)
        self.assertIsNone(self.reload().end_date)
        self.assertEqual(Turnout.objects.count(), 1)
        self.assertEqual(self.usage(self.paddock), USAGE.TURNOUT)

    def test_a_move_to_another_barn_keeps_it(self):
        PlacementService.move_horse(self.horse, new_location=self.other_barn, move_date=self.day(3))
        self.assertIsNone(self.reload().end_date)
        self.assertEqual(Turnout.objects.count(), 1)
        self.assertEqual(self.usage(self.paddock), USAGE.TURNOUT)
        # One unbroken turnout period on the field, not a rest and a restart.
        self.assertEqual(
            list(self.paddock.usage_periods.filter(start_date__gte=self.day(20))
                 .values_list('usage', flat=True)), [USAGE.TURNOUT])

    def test_a_move_to_live_in_a_field_ends_it(self):
        PlacementService.move_horse(self.horse, new_location=self.hill, move_date=self.day(3))
        self.assertEqual(self.reload().end_date, self.day(4))
        self.assertIsNone(open_turnout(self.horse))
        self.assertEqual(self.usage(self.paddock), USAGE.RESTED)

    def test_a_move_to_live_on_its_own_turnout_field(self):
        PlacementService.move_horse(self.horse, new_location=self.paddock, move_date=self.day(3))
        self.assertIsNone(open_turnout(self.horse))
        self.assertEqual(self.usage(self.paddock), USAGE.HORSES)

    def test_coming_back_to_a_barn_later_does_not_resume_it(self):
        PlacementService.move_horse(self.horse, new_location=self.hill, move_date=self.day(10))
        PlacementService.move_horse(
            self.fresh(self.horse), new_location=self.barn, move_date=self.day(4))
        self.assertIsNone(open_turnout(self.horse))

    def test_a_stopped_turnout_is_not_given_back_by_a_rate_change(self):
        TurnoutService.stop(self.horse, self.day(3))
        other = RateType.objects.create(name='Box rest', daily_rate=Decimal('30'))
        PlacementService.move_horse(
            self.horse, new_location=self.barn, move_date=self.day(3), new_rate_type=other)
        self.assertIsNone(open_turnout(self.horse))

    def test_deleting_the_placement_takes_it_too(self):
        self.horse.placements.get().delete()
        self.assertIsNone(open_turnout(self.horse))
        self.assertFalse(stray_turnouts().exists())

    def test_saving_the_placement_again_changes_nothing(self):
        placement = self.horse.placements.get()
        placement.save()
        placement.save()
        turnout = self.reload()
        self.assertEqual((turnout.start_date, turnout.end_date), (self.day(20), None))

    def test_billing_is_unchanged(self):
        other = self.horse_in(self.barn, 'Control')
        start, end = self.day(29), self.today
        charge = lambda horse: [  # noqa: E731
            (p.get_days_in_period(start, end), p.calculate_charge(start, end))
            for p in horse.placements.order_by('start_date')
        ]
        self.assertEqual(charge(self.horse), charge(other))
        TurnoutService.change(self.horse, self.hill, self.day(5))
        TurnoutService.stop(self.horse, self.day(2))
        self.assertEqual(charge(self.horse), charge(other))


class CountsTests(TurnoutTestCase):
    def setUp(self):
        self.horse_in(self.paddock, 'Resident 1', days_ago=20)
        self.horse_in(self.paddock, 'Resident 2', days_ago=20)
        for name in ('Marram', 'Sixpence', 'Low Tide'):
            TurnoutService.start(self.horse_in(self.barn, name), self.paddock, self.day(5))

    def test_with_occupancy_keeps_the_two_numbers_apart(self):
        rows = {loc.name: loc for loc in Location.objects.active().with_occupancy()}
        self.assertEqual((rows['Back Paddock'].horse_count, rows['Back Paddock'].turnout_count), (2, 3))
        self.assertEqual((rows['Top Barn'].horse_count, rows['Top Barn'].turnout_count), (3, 0))
        self.assertEqual((rows['Hill Field'].horse_count, rows['Hill Field'].turnout_count), (0, 0))

    def test_turnout_counts(self):
        self.assertEqual(turnout_counts(), {self.paddock.pk: 3})

    def test_turnout_horses_name_the_barn_each_lives_in(self):
        horses = turnout_horses(self.paddock)
        self.assertEqual([h.name for h in horses], ['Low Tide', 'Marram', 'Sixpence'])
        self.assertEqual({h.home_name for h in horses}, {'Top Barn'})

    def test_annotate_turnout(self):
        rows = {h.name: h for h in annotate_turnout(Horse.objects.all())}
        self.assertEqual(rows['Marram'].turnout_name, 'Back Paddock')
        self.assertIsNone(rows['Resident 1'].turnout_name)

    def test_the_picker_offers_ground_on_the_same_site_first_and_no_barns(self):
        Location.objects.create(name='Far Meadow', site='Other Farm')
        choices = turnout_choices(self.barn)
        self.assertEqual(choices[0][0], 'Manor Farm')
        self.assertEqual([name for _pk, name in choices[0][1]], ['Back Paddock', 'Hill Field'])
        self.assertEqual(choices[1][0], 'Other Farm')
        names = [name for _site, group in choices for _pk, name in group]
        self.assertNotIn('Top Barn', names)
        self.assertNotIn('Lower Barn', names)

    def test_nothing_strays_and_a_row_written_behind_the_service_is_found(self):
        self.assertFalse(stray_turnouts().exists())
        loose = Horse.objects.create(name='Loose')
        Placement.objects.create(
            horse=loose, owner=self.owner, location=self.hill, rate_type=self.rate,
            start_date=self.day(3))
        stray = Turnout.objects.create(horse=loose, location=self.paddock, start_date=self.day(2))
        self.assertEqual(list(stray_turnouts()), [stray])
