"""Who is in which stable (docs/BARNS_AND_STABLES_PLAN.md, phase 2a).

A stable stay sits beside the placement and has to follow it whichever
way the placement is written. The cases here are the rows of the table in
section 6.3 of the plan, and the ones that are easy to get wrong sit at
the top: a rate change is a "move" to the same barn, and the horse must
keep its stable through it.
"""

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from core.models import (
    Horse, Location, Owner, OwnershipShare, Placement, RateType, StableStay,
)
from core.services import PlacementService
from core.stables import (
    StableOccupied, StableService, add_stables, annotate_stable, open_stay,
    remove_stable,
)
from health.models import BreedingRecord
from health.services import record_foaling

BARN = Location.LocationType.BARN


class StayTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = Owner.objects.create(name='Jo Bloggs')
        cls.box = RateType.objects.create(name='Box care', daily_rate=Decimal('20'))
        cls.box_rest = RateType.objects.create(name='Box rest', daily_rate=Decimal('30'))
        cls.today = timezone.localdate()
        cls.barn = Location.objects.create(
            name='Top Barn', site='Manor Farm', location_type=BARN, layout='aisle')
        add_stables(cls.barn, 10)
        cls.other_barn = Location.objects.create(
            name='Lower Barn', site='Manor Farm', location_type=BARN, layout='row')
        add_stables(cls.other_barn, 4)
        cls.field = Location.objects.create(name='Back Paddock', site='Manor Farm')

    def stable(self, label, barn=None):
        return (barn or self.barn).stables.get(label=str(label))

    def horse_in(self, location, name='Marram', days_ago=30, rate=None):
        horse = Horse.objects.create(name=name)
        Placement.objects.create(
            horse=horse, owner=self.owner, location=location,
            rate_type=rate or self.box, start_date=self.today - timedelta(days=days_ago),
        )
        return horse

    def day(self, days_ago):
        return self.today - timedelta(days=days_ago)


class AssignTests(StayTestCase):
    def test_put_a_horse_in_a_stable(self):
        horse = self.horse_in(self.barn)
        stay = StableService.assign(horse, self.stable(7), self.day(5))
        self.assertEqual((stay.stable.label, stay.start_date, stay.end_date), ('7', self.day(5), None))
        self.assertEqual(open_stay(horse), stay)

    def test_today_is_the_default_day(self):
        horse = self.horse_in(self.barn)
        self.assertEqual(StableService.assign(horse, self.stable(7)).start_date, self.today)

    def test_moving_stable_ends_the_old_stay_the_day_before(self):
        horse = self.horse_in(self.barn)
        first = StableService.assign(horse, self.stable(7), self.day(5))
        second = StableService.assign(horse, self.stable(9), self.day(2))
        first.refresh_from_db()
        self.assertEqual(first.end_date, self.day(3))
        self.assertFalse(first.closed_with_placement)
        self.assertEqual(second.start_date, self.day(2))

    def test_a_stable_change_is_not_a_placement_change(self):
        horse = self.horse_in(self.barn)
        StableService.assign(horse, self.stable(7), self.day(5))
        before = list(Placement.objects.values_list('pk', 'start_date', 'end_date', 'updated_at'))
        StableService.assign(horse, self.stable(9), self.day(2))
        self.assertEqual(
            list(Placement.objects.values_list('pk', 'start_date', 'end_date', 'updated_at')), before)

    def test_a_same_day_change_leaves_no_stay_of_no_days(self):
        horse = self.horse_in(self.barn)
        StableService.assign(horse, self.stable(7), self.today)
        StableService.assign(horse, self.stable(9), self.today)
        self.assertEqual(
            list(StableStay.objects.filter(horse=horse).values_list('stable__label', flat=True)), ['9'])

    def test_assigning_the_same_stable_again_changes_nothing(self):
        horse = self.horse_in(self.barn)
        stay = StableService.assign(horse, self.stable(7), self.day(5))
        self.assertEqual(StableService.assign(horse, self.stable(7), self.today), stay)
        self.assertEqual(StableStay.objects.count(), 1)

    def test_the_horse_must_live_in_that_barn(self):
        horse = self.horse_in(self.field)
        with self.assertRaisesMessage(ValidationError, 'is not in Top Barn'):
            StableService.assign(horse, self.stable(7))
        with self.assertRaises(ValidationError):
            StableService.assign(Horse.objects.create(name='Nowhere'), self.stable(7))

    def test_not_a_gap_and_not_out_of_use(self):
        from core.stables import add_gap
        horse = self.horse_in(self.barn)
        with self.assertRaises(ValidationError):
            StableService.assign(horse, add_gap(self.barn, 'Tack room'))
        off = self.stable(3)
        off.in_use = False
        off.save()
        with self.assertRaisesMessage(ValidationError, 'out of use'):
            StableService.assign(horse, off)

    def test_dates_that_cannot_be(self):
        horse = self.horse_in(self.barn, days_ago=10)
        with self.assertRaises(ValidationError):
            StableService.assign(horse, self.stable(7), self.today + timedelta(days=1))
        with self.assertRaises(ValidationError):
            StableService.assign(horse, self.stable(7), self.day(11))
        StableService.assign(horse, self.stable(7), self.day(4))
        with self.assertRaises(ValidationError):
            StableService.assign(horse, self.stable(9), self.day(6))

    def test_an_occupied_stable_asks_before_it_is_shared(self):
        marram = self.horse_in(self.barn, 'Marram')
        sixpence = self.horse_in(self.barn, 'Sixpence')
        StableService.assign(marram, self.stable(7))
        with self.assertRaises(StableOccupied) as raised:
            StableService.assign(sixpence, self.stable(7))
        self.assertEqual(raised.exception.occupants, [marram])
        self.assertIn('Marram', raised.exception.messages[0])
        self.assertIsNone(open_stay(sixpence))

        StableService.assign(sixpence, self.stable(7), share=True)
        self.assertEqual(
            StableStay.objects.filter(stable=self.stable(7), end_date__isnull=True).count(), 2)

    def test_three_can_share(self):
        horses = [self.horse_in(self.barn, name) for name in ('A', 'B', 'C')]
        for horse in horses:
            StableService.assign(horse, self.stable(1), share=True)
        self.assertEqual(StableStay.objects.filter(end_date__isnull=True).count(), 3)

    def test_one_open_stay_a_horse_is_a_database_rule(self):
        from django.db import IntegrityError, transaction
        horse = self.horse_in(self.barn)
        StableService.assign(horse, self.stable(7))
        with self.assertRaises(IntegrityError), transaction.atomic():
            StableStay.objects.create(horse=horse, stable=self.stable(9), start_date=self.today)


class VacateSwapUndoTests(StayTestCase):
    def test_vacate_leaves_the_horse_in_the_barn(self):
        horse = self.horse_in(self.barn)
        StableService.assign(horse, self.stable(7), self.day(5))
        ended = StableService.vacate(horse, self.today)
        self.assertEqual(ended.end_date, self.day(1))
        self.assertIsNone(open_stay(horse))
        self.assertEqual(horse.placements.get().location, self.barn)
        self.assertIsNone(horse.placements.get().end_date)

    def test_vacate_with_no_stable_is_nothing(self):
        self.assertIsNone(StableService.vacate(self.horse_in(self.barn)))

    def test_swap(self):
        marram = self.horse_in(self.barn, 'Marram')
        sixpence = self.horse_in(self.barn, 'Sixpence')
        StableService.assign(marram, self.stable(7), self.day(5))
        StableService.assign(sixpence, self.stable(9), self.day(5))
        StableService.swap(marram, sixpence, self.today)
        self.assertEqual(open_stay(marram).stable.label, '9')
        self.assertEqual(open_stay(sixpence).stable.label, '7')
        self.assertEqual(StableStay.objects.filter(end_date=self.day(1)).count(), 2)

    def test_swap_needs_two_stables_in_one_barn(self):
        marram = self.horse_in(self.barn, 'Marram')
        sixpence = self.horse_in(self.barn, 'Sixpence')
        elsewhere = self.horse_in(self.other_barn, 'Low Tide')
        StableService.assign(marram, self.stable(7))
        StableService.assign(elsewhere, self.stable(1, self.other_barn))
        with self.assertRaisesMessage(ValidationError, 'has no stable'):
            StableService.swap(marram, sixpence)
        with self.assertRaisesMessage(ValidationError, 'inside one barn'):
            StableService.swap(marram, elsewhere)

    def test_undo_a_move_picks_the_old_stay_back_up(self):
        horse = self.horse_in(self.barn)
        first = StableService.assign(horse, self.stable(7), self.day(5))
        second = StableService.assign(horse, self.stable(9), self.today)
        back = StableService.undo(second, previous_stable=self.stable(7))
        self.assertEqual(back.pk, first.pk)
        self.assertIsNone(back.end_date)
        self.assertEqual(StableStay.objects.count(), 1)

    def test_undo_a_same_day_move_goes_back_to_the_stable_it_left(self):
        horse = self.horse_in(self.barn)
        StableService.assign(horse, self.stable(7), self.today)
        second = StableService.assign(horse, self.stable(9), self.today)
        back = StableService.undo(second, previous_stable=self.stable(7))
        self.assertEqual(back.stable.label, '7')
        self.assertEqual(StableStay.objects.count(), 1)

    def test_undo_a_first_assignment_puts_the_horse_back_in_the_barn(self):
        horse = self.horse_in(self.barn)
        stay = StableService.assign(horse, self.stable(7))
        self.assertIsNone(StableService.undo(stay))
        self.assertFalse(StableStay.objects.exists())

    def test_only_the_current_stay_can_be_undone(self):
        horse = self.horse_in(self.barn)
        first = StableService.assign(horse, self.stable(7), self.day(5))
        StableService.assign(horse, self.stable(9), self.today)
        first.refresh_from_db()
        with self.assertRaises(ValidationError):
            StableService.undo(first)


class FollowsThePlacementTests(StayTestCase):
    """Section 6.3: every way a placement changes, and what the stay does."""

    def setUp(self):
        self.horse = self.horse_in(self.barn, days_ago=30)
        self.stay = StableService.assign(self.horse, self.stable(7), self.day(20))

    def reload(self):
        self.stay.refresh_from_db()
        return self.stay

    # ── the one that is easy to get wrong ────────────────────────────
    def test_a_rate_change_keeps_the_stable_and_the_one_stay(self):
        PlacementService.move_horse(
            self.horse, new_location=self.barn, move_date=self.day(3),
            new_rate_type=self.box_rest,
        )
        self.assertEqual(self.horse.placements.count(), 2)
        stay = self.reload()
        self.assertIsNone(stay.end_date)
        self.assertFalse(stay.closed_with_placement)
        self.assertEqual(stay.start_date, self.day(20))
        self.assertEqual(StableStay.objects.filter(horse=self.horse).count(), 1)

    def test_two_rate_changes_still_one_stay(self):
        PlacementService.move_horse(
            self.horse, new_location=self.barn, move_date=self.day(6), new_rate_type=self.box_rest)
        # A fresh object: current_placement is cached on the instance.
        PlacementService.move_horse(
            Horse.objects.get(pk=self.horse.pk), new_location=self.barn,
            move_date=self.day(2), new_rate_type=self.box)
        self.assertEqual(self.horse.placements.count(), 3)
        self.assertEqual(StableStay.objects.filter(horse=self.horse).count(), 1)
        self.assertIsNone(self.reload().end_date)

    def test_a_rate_change_does_not_give_back_a_stable_the_horse_was_taken_out_of(self):
        StableService.vacate(self.horse, self.day(3))
        PlacementService.move_horse(
            self.horse, new_location=self.barn, move_date=self.day(3), new_rate_type=self.box_rest)
        self.assertIsNone(open_stay(self.horse))

    # ── leaving ──────────────────────────────────────────────────────
    def test_departure_ends_the_stay_the_same_day(self):
        PlacementService.depart_horse(self.horse, self.day(1))
        stay = self.reload()
        self.assertEqual(stay.end_date, self.day(1))
        self.assertTrue(stay.closed_with_placement)

    def test_a_scheduled_departure_changes_nothing(self):
        PlacementService.depart_horse(self.horse, self.today + timedelta(days=7))
        self.assertIsNone(self.reload().end_date)

    def test_undoing_a_departure_gives_the_stable_back(self):
        PlacementService.depart_horse(self.horse, self.day(1))
        PlacementService.cancel_departure(self.horse)
        stay = self.reload()
        self.assertIsNone(stay.end_date)
        self.assertFalse(stay.closed_with_placement)

    def test_undoing_a_departure_does_not_evict_whoever_moved_in(self):
        PlacementService.depart_horse(self.horse, self.day(1))
        other = self.horse_in(self.barn, 'Sixpence')
        StableService.assign(other, self.stable(7))
        PlacementService.cancel_departure(self.horse)
        self.assertIsNone(open_stay(self.horse))
        self.assertEqual(open_stay(other).stable.label, '7')

    def test_undoing_a_departure_skips_a_stable_now_out_of_use(self):
        PlacementService.depart_horse(self.horse, self.day(1))
        stable = self.stable(7)
        stable.in_use = False
        stable.save()
        PlacementService.cancel_departure(self.horse)
        self.assertIsNone(open_stay(self.horse))

    # ── moving ───────────────────────────────────────────────────────
    def test_a_move_to_another_barn_ends_the_stay_the_day_before(self):
        PlacementService.move_horse(self.horse, new_location=self.other_barn, move_date=self.day(4))
        stay = self.reload()
        self.assertEqual(stay.end_date, self.day(5))
        self.assertIsNone(open_stay(self.horse))

    def test_a_move_to_a_field_ends_the_stay(self):
        PlacementService.move_horse(self.horse, new_location=self.field, move_date=self.day(4))
        self.assertEqual(self.reload().end_date, self.day(5))

    def test_coming_back_later_does_not_resume_an_old_stay(self):
        PlacementService.move_horse(self.horse, new_location=self.field, move_date=self.day(10))
        PlacementService.move_horse(
            Horse.objects.get(pk=self.horse.pk), new_location=self.barn, move_date=self.day(4))
        self.assertIsNone(open_stay(self.horse))

    # ── corrections ──────────────────────────────────────────────────
    def test_an_open_placement_corrected_to_another_location_drops_the_stay(self):
        placement = self.horse.placements.get()
        placement.location = self.other_barn
        placement.save()
        # The horse was never in Top Barn, so it was never in stable 7.
        self.assertFalse(StableStay.objects.filter(horse=self.horse).exists())

    def test_moving_the_arrival_later_moves_the_stay_start_with_it(self):
        placement = self.horse.placements.get()
        placement.start_date = self.day(12)
        placement.save()
        self.assertEqual(self.reload().start_date, self.day(12))

    def test_a_stay_can_start_before_the_placement_after_a_rate_change(self):
        PlacementService.move_horse(
            self.horse, new_location=self.barn, move_date=self.day(3), new_rate_type=self.box_rest)
        current = self.horse.placements.get(end_date__isnull=True)
        current.notes = 'Vet says a fortnight'
        current.save()
        # Saving the new placement again must not drag the stay's start
        # up to the day the rate changed.
        self.assertEqual(self.reload().start_date, self.day(20))

    def test_saving_a_placement_twice_changes_nothing(self):
        placement = self.horse.placements.get()
        placement.save()
        placement.save()
        stay = self.reload()
        self.assertEqual((stay.start_date, stay.end_date), (self.day(20), None))

    def test_deleting_the_placement_takes_the_stay_with_it(self):
        self.horse.placements.get().delete()
        self.assertFalse(StableStay.objects.filter(horse=self.horse).exists())

    def test_deleting_the_second_half_of_a_rate_change_ends_the_stay_with_the_first(self):
        PlacementService.move_horse(
            self.horse, new_location=self.barn, move_date=self.day(3), new_rate_type=self.box_rest)
        self.horse.placements.get(end_date__isnull=True).delete()
        stay = self.reload()
        self.assertEqual(stay.end_date, self.day(4))
        self.assertTrue(stay.closed_with_placement)


class StableGuardsTests(StayTestCase):
    def test_a_stable_with_history_cannot_be_removed(self):
        horse = self.horse_in(self.barn)
        StableService.assign(horse, self.stable(7), self.day(5))
        StableService.vacate(horse, self.today)
        with self.assertRaisesMessage(ValidationError, 'history'):
            remove_stable(self.stable(7))
        remove_stable(self.stable(8))

    def test_barn_delete_blockers_count_stays(self):
        horse = self.horse_in(self.barn)
        StableService.assign(horse, self.stable(7))
        self.assertTrue(any('stable record' in reason for reason in self.barn.delete_blockers()))

    def test_annotate_stable(self):
        from core.models import Horse as HorseModel
        placed = self.horse_in(self.barn, 'Marram')
        loose = self.horse_in(self.barn, 'Sixpence')
        StableService.assign(placed, self.stable(7))
        rows = {h.name: h for h in annotate_stable(HorseModel.objects.all())}
        self.assertEqual(rows['Marram'].stable_label, '7')
        self.assertEqual(rows['Marram'].stable_position, 7)
        self.assertEqual(rows['Marram'].stable_pk, self.stable(7).pk)
        self.assertIsNone(rows['Sixpence'].stable_label)
        self.assertIsNotNone(loose)


class FoalingTests(StayTestCase):
    def setUp(self):
        self.pair = RateType.objects.create(name='Mare and foal', daily_rate=Decimal('35'))
        self.foal_rate = RateType.objects.create(name='Foal at foot', daily_rate=Decimal('0'))
        self.mare = Horse.objects.create(name='Vivian', sex='mare')
        OwnershipShare.objects.create(
            horse=self.mare, owner=self.owner, share_percentage=100, is_primary_contact=True)
        Placement.objects.create(
            horse=self.mare, owner=self.owner, location=self.barn, rate_type=self.box,
            start_date=self.day(60),
        )
        self.record = BreedingRecord.objects.create(
            mare=self.mare, stallion_name='Hip Hop',
            date_covered=self.today - timedelta(days=338), status='confirmed',
        )
        self.mare_stay = StableService.assign(self.mare, self.stable(4), self.day(40))

    def test_the_mare_keeps_her_stable_and_the_foal_joins_her(self):
        foal = record_foaling(
            self.record, foal_dob=self.day(1), foal_sex='filly', foal_name='Little Viv',
            mare_rate_type=self.pair, place_foal=True, foal_rate_type=self.foal_rate,
        )
        self.assertEqual(self.mare.placements.count(), 2)
        self.mare_stay.refresh_from_db()
        self.assertIsNone(self.mare_stay.end_date)
        self.assertEqual(StableStay.objects.filter(horse=self.mare).count(), 1)
        foal_stay = open_stay(foal)
        self.assertEqual(foal_stay.stable, self.stable(4))
        self.assertEqual(foal_stay.start_date, self.day(1))

    def test_a_mare_with_no_stable_gives_the_foal_none(self):
        StableService.vacate(self.mare, self.day(10))
        foal = record_foaling(
            self.record, foal_dob=self.day(1), foal_sex='colt', foal_name='Bob',
            place_foal=True, foal_rate_type=self.foal_rate,
        )
        self.assertIsNone(open_stay(foal))


class InvoiceUnchangedTests(StayTestCase):
    """Principle 3.2: nothing in billing reads a stable."""

    def charge(self, horse):
        start, end = self.day(29), self.today
        return [
            (p.rate_type.name, p.get_days_in_period(start, end), p.calculate_charge(start, end))
            for p in horse.placements.order_by('start_date')
        ]

    def test_stable_changes_do_not_change_what_is_billed(self):
        horse = self.horse_in(self.barn, days_ago=40)
        before = self.charge(horse)
        StableService.assign(horse, self.stable(7), self.day(20))
        StableService.assign(horse, self.stable(9), self.day(10))
        StableService.vacate(horse, self.day(5))
        StableService.assign(horse, self.stable(2), self.day(2))
        self.assertEqual(self.charge(horse), before)
        self.assertEqual(horse.placements.count(), 1)
